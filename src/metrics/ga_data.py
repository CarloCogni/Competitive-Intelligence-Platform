# metrics/ga_data.py
"""
Historical event builder for GA compound-rule optimization.

Reconstructs labeled (entity, time_window) events from the Metric and
AlertSeen tables. Each event contains a delta snapshot (pct_change per
metric key) and a label derived from explicit user feedback:

    True  — at least one alert in the window was dismissed with useful=True.
    False — all dismissed alerts were marked useful=False, OR no alert
            fired at all. Only explicit negative feedback counts.
    None  — alerts exist but have no explicit feedback (undismissed, bulk
            dismiss, or too recent). Excluded from fitness evaluation.
"""

import logging
from datetime import timedelta

from django.db import models
from django.utils import timezone

from metrics.ga_optimizer import HistoricalEvent
from metrics.models import Alert, AlertSeen, DeltaThreshold, Metric

logger = logging.getLogger(__name__)

# Alerts older than this without dismissal are labeled as false positives.
LABEL_GRACE_DAYS = 14

# Minimum metric keys with data for an event to be usable.
MIN_METRIC_KEYS = 2

# Default window size for event aggregation (days).
# Automatically reduced for entities with short data spans.
WINDOW_DAYS = 7

# Minimum window size (days). Allows the GA to work with fresh deployments.
MIN_WINDOW_DAYS = 1


def get_available_metric_keys() -> list[str]:
    """Return metric keys that have a DeltaThreshold configured, sorted."""
    return list(DeltaThreshold.objects.order_by("metric_key").values_list("metric_key", flat=True))


def build_historical_events(
    lookback_days: int = 90,
) -> list[HistoricalEvent]:
    """
    Build labeled historical events for GA fitness evaluation.

    For each entity, divides the lookback period into weekly windows and:
    1. Computes pct_change for every configured metric key.
    2. Labels the window based on Alert + AlertSeen presence.

    Returns a list of HistoricalEvent objects ready for FitnessEvaluator.
    """
    now = timezone.now()
    cutoff = now - timedelta(days=lookback_days)
    label_cutoff = now - timedelta(days=LABEL_GRACE_DAYS)

    metric_keys = get_available_metric_keys()
    if not metric_keys:
        logger.warning("No DeltaThreshold rows found — cannot build events.")
        return []

    # Load all entity IDs that have metric data in the lookback window.
    entity_ids = list(
        Metric.objects.filter(captured_at__gte=cutoff)
        .order_by("entity_id")
        .values_list("entity_id", flat=True)
        .distinct()
    )

    logger.info(
        "Building historical events: %d entities, %d metric keys, %d-day lookback.",
        len(entity_ids),
        len(metric_keys),
        lookback_days,
    )

    events: list[HistoricalEvent] = []

    for entity_id in entity_ids:
        entity_events = _build_entity_events(
            entity_id=entity_id,
            metric_keys=metric_keys,
            cutoff=cutoff,
            now=now,
            label_cutoff=label_cutoff,
        )
        events.extend(entity_events)

    n_pos = sum(1 for e in events if e.label is True)
    n_neg = sum(1 for e in events if e.label is False)
    n_unk = sum(1 for e in events if e.label is None)

    logger.info(
        "Built %d events: %d positive, %d negative, %d unknown (excluded).",
        len(events),
        n_pos,
        n_neg,
        n_unk,
    )

    return events


def get_recent_deltas(weeks: int = 4) -> list[dict[str, float]]:
    """
    Collect recent metric delta snapshots for GA staleness checking.

    Computes per-entity, per-week deltas for the last N weeks and returns
    them as a flat list of delta dicts. Used by FitnessEvaluator to penalize
    rules that wouldn't fire on recent data.
    """
    now = timezone.now()
    cutoff = now - timedelta(weeks=weeks)
    metric_keys = get_available_metric_keys()

    if not metric_keys:
        return []

    entity_ids = list(
        Metric.objects.filter(captured_at__gte=cutoff)
        .order_by("entity_id")
        .values_list("entity_id", flat=True)
        .distinct()
    )

    recent: list[dict[str, float]] = []
    for entity_id in entity_ids:
        # One-week windows over the recent period
        window_start = cutoff
        while window_start < now:
            window_end = window_start + timedelta(weeks=1)
            deltas = _compute_window_deltas(entity_id, metric_keys, window_start, window_end)
            if deltas:
                recent.append(deltas)
            window_start = window_end

    logger.info("Collected %d recent delta snapshots for staleness checking.", len(recent))
    return recent


def _compute_window_size(entity_id: int, cutoff) -> int:
    """
    Choose a window size (in days) based on the entity's actual data span.

    For entities with months of data, use the default WINDOW_DAYS (7).
    For entities with only a few days of data (fresh deployments), shrink
    the window so that at least 2 windows can be formed — the first window
    provides baseline data for the second.
    """
    dates = Metric.objects.filter(entity_id=entity_id, captured_at__gte=cutoff).aggregate(
        first=models.Min("captured_at"),
        last=models.Max("captured_at"),
    )
    if not dates["first"] or not dates["last"]:
        return WINDOW_DAYS

    span_days = max(1, (dates["last"] - dates["first"]).days)

    if span_days >= WINDOW_DAYS * 3:
        # Enough data for standard weekly windows
        return WINDOW_DAYS

    # Shrink window to fit at least 3 windows in the span (2 usable events)
    return max(MIN_WINDOW_DAYS, span_days // 3)


def _build_entity_events(
    entity_id: int,
    metric_keys: list[str],
    cutoff,
    now,
    label_cutoff,
) -> list[HistoricalEvent]:
    """Build events for a single entity using adaptive window sizes."""
    events: list[HistoricalEvent] = []

    window_days = _compute_window_size(entity_id, cutoff)

    # Walk through windows from cutoff to now.
    window_start = cutoff
    while window_start < now:
        window_end = window_start + timedelta(days=window_days)
        if window_end > now:
            window_end = now

        # Compute deltas for this window
        deltas = _compute_window_deltas(
            entity_id=entity_id,
            metric_keys=metric_keys,
            window_start=window_start,
            window_end=window_end,
        )

        # Skip windows with insufficient metric coverage
        if len(deltas) < MIN_METRIC_KEYS:
            window_start = window_end
            continue

        # Determine label from alerts in this window
        label = _label_window(
            entity_id=entity_id,
            window_start=window_start,
            window_end=window_end,
            label_cutoff=label_cutoff,
        )

        events.append(
            HistoricalEvent(
                entity_id=entity_id,
                timestamp=window_end,
                deltas=deltas,
                label=label,
            )
        )

        window_start = window_end

    return events


def _compute_window_deltas(
    entity_id: int,
    metric_keys: list[str],
    window_start,
    window_end,
) -> dict[str, float]:
    """
    Compute pct_change for each metric key within a time window.

    Latest reading in the window vs. most recent reading before the window.
    Returns only keys where both readings exist and baseline is nonzero.
    """
    deltas: dict[str, float] = {}

    for key in metric_keys:
        # Latest value in the window
        latest = (
            Metric.objects.filter(
                entity_id=entity_id,
                key=key,
                captured_at__gte=window_start,
                captured_at__lt=window_end,
            )
            .order_by("-captured_at")
            .values_list("value", flat=True)
            .first()
        )

        if latest is None:
            continue

        # Baseline: most recent value before the window
        baseline = (
            Metric.objects.filter(
                entity_id=entity_id,
                key=key,
                captured_at__lt=window_start,
            )
            .order_by("-captured_at")
            .values_list("value", flat=True)
            .first()
        )

        if baseline is None or baseline == 0:
            continue

        deltas[key] = (latest - baseline) / baseline

    return deltas


def _label_window(
    entity_id: int,
    window_start,
    window_end,
    label_cutoff,
) -> bool | None:
    """
    Determine the label for an (entity, window) pair using explicit feedback.

    Labeling logic (only explicit user actions generate training labels):
        - No alert fired in window: False (nothing noteworthy happened)
        - Any alert dismissed with useful=True: True (positive)
        - All dismissed alerts have useful=False (and none True): False (explicit noise)
        - Dismissed with useful=None only (bulk/legacy): None (excluded)
        - Alerts exist but undismissed: None (excluded — no signal either way)
    """
    # Find alerts for this entity in the window
    window_alerts = Alert.objects.filter(
        entity_id=entity_id,
        created_at__gte=window_start,
        created_at__lt=window_end,
    )

    if not window_alerts.exists():
        # No alert fired — negative sample
        return False

    # Check for explicit feedback via AlertSeen records with useful != None
    feedback_records = AlertSeen.objects.filter(
        alert__in=window_alerts,
        useful__isnull=False,
    )

    if feedback_records.filter(useful=True).exists():
        # At least one alert was marked as useful — positive label
        return True

    if feedback_records.filter(useful=False).exists():
        # All explicit feedback is negative — negative label
        return False

    # No explicit feedback (undismissed, bulk dismiss, or legacy) — exclude
    return None

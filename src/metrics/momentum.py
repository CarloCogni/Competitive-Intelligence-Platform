# metrics/momentum.py
"""
Momentum score computation engine.

Aggregates per-metric 7-day velocities into a single entity-level composite
score using cross-entity z-score normalization. This answers: "How fast is
each entity moving relative to the field, and is it accelerating?"

Algorithm:
    1. For each (entity, metric_key): velocity = latest_value - value_7_days_ago
    2. Negate velocity for inverted metrics (lower-is-better keys)
    3. Per metric key: compute population mean and stddev across all entities
    4. Z-score each velocity, capped at ±z_cap to prevent outlier domination
    5. Composite score = weighted average of available z-scores per entity
    6. Acceleration = score_today - score_previous

Usage:
    from metrics.momentum import compute_momentum_scores
    results = compute_momentum_scores()          # all entities
    results = compute_momentum_scores(dry_run=True)  # preview only
"""

import logging
import statistics
from datetime import timedelta
from typing import NamedTuple

from django.utils import timezone

from core.models import Entity
from metrics.models import Metric, MomentumConfig, MomentumScore

logger = logging.getLogger(__name__)

# Metrics where a lower value is better — velocity must be negated
INVERTED_METRICS: set[str] = {
    "github_latest_release_days_ago",
    "appstore_last_update_days_ago",
    "playstore_last_update_days_ago",
    "changelog_latest_release_days_ago",
}

# Velocity lookback window in days
VELOCITY_WINDOW_DAYS: int = 7


class EntityVelocity(NamedTuple):
    """Raw velocity for one (entity, metric_key) pair."""

    entity_id: int
    metric_key: str
    velocity: float


def _compute_velocities() -> dict[int, dict[str, float]]:
    """
    Compute 7-day velocity for every (entity, metric_key) pair.

    Returns a nested dict: {entity_id: {metric_key: velocity}}.
    Inverted metrics have their velocity negated so positive always means "good".
    """
    now = timezone.now()
    cutoff = now - timedelta(days=VELOCITY_WINDOW_DAYS)

    # Get all entities
    entity_ids = list(Entity.objects.values_list("pk", flat=True))
    if not entity_ids:
        return {}

    # Get all distinct metric keys that have data
    metric_keys = list(Metric.objects.values_list("key", flat=True).distinct())
    if not metric_keys:
        return {}

    velocities: dict[int, dict[str, float]] = {}

    for key in metric_keys:
        # For each entity, get the latest value and the baseline value (>= 7 days ago)
        for entity_id in entity_ids:
            latest_qs = (
                Metric.objects.filter(entity_id=entity_id, key=key, value__isnull=False)
                .order_by("-captured_at")
                .values_list("value", flat=True)[:1]
            )
            latest_val = list(latest_qs)
            if not latest_val:
                continue

            baseline_qs = (
                Metric.objects.filter(
                    entity_id=entity_id,
                    key=key,
                    value__isnull=False,
                    captured_at__lte=cutoff,
                )
                .order_by("-captured_at")
                .values_list("value", flat=True)[:1]
            )
            baseline_val = list(baseline_qs)
            if not baseline_val:
                continue

            vel = latest_val[0] - baseline_val[0]

            # Negate inverted metrics so positive = good
            if key in INVERTED_METRICS:
                vel = -vel

            velocities.setdefault(entity_id, {})[key] = vel

    return velocities


def _z_score_normalize(
    velocities: dict[int, dict[str, float]],
    z_cap: float,
) -> dict[str, tuple[float, float]]:
    """
    Compute per-metric-key population mean and stddev from all entity velocities.

    Returns: {metric_key: (mean, stddev)}.
    """
    # Collect all velocities per key
    key_values: dict[str, list[float]] = {}
    for entity_vels in velocities.values():
        for key, vel in entity_vels.items():
            key_values.setdefault(key, []).append(vel)

    stats: dict[str, tuple[float, float]] = {}
    for key, vals in key_values.items():
        if len(vals) < 2:
            # Not enough data for meaningful z-score
            stats[key] = (vals[0] if vals else 0.0, 0.0)
        else:
            mu = statistics.mean(vals)
            sigma = statistics.stdev(vals)
            stats[key] = (mu, sigma)

    return stats


def compute_momentum_scores(
    entity_slug: str | None = None,
    dry_run: bool = False,
) -> list[dict]:
    """
    Compute and persist momentum scores for all (or one) entity.

    Args:
        entity_slug: If provided, compute only for this entity.
        dry_run: If True, compute but do not persist to the database.

    Returns:
        List of dicts with entity info and computed scores (for CLI output).
    """
    config = MomentumConfig.get()
    weights = config.weights if isinstance(config.weights, dict) else {}
    z_cap = config.z_cap

    # Step 1: Compute raw velocities
    all_velocities = _compute_velocities()
    if not all_velocities:
        logger.info("No metric data available for momentum computation.")
        return []

    # Step 2: Compute population stats for z-score normalization
    pop_stats = _z_score_normalize(all_velocities, z_cap)

    # Step 3: Compute per-entity composite scores
    entities = Entity.objects.all()
    if entity_slug:
        entities = entities.filter(slug=entity_slug)

    entity_map = {e.pk: e for e in entities}

    # Fetch previous scores for acceleration computation
    prev_scores: dict[int, float] = {}
    for entity_id in entity_map:
        prev = (
            MomentumScore.objects.filter(entity_id=entity_id)
            .order_by("-computed_at")
            .values_list("score", flat=True)
            .first()
        )
        if prev is not None:
            prev_scores[entity_id] = prev

    results: list[dict] = []
    scores_to_create: list[MomentumScore] = []
    now = timezone.now()

    for entity_id, entity in entity_map.items():
        entity_vels = all_velocities.get(entity_id)
        if not entity_vels:
            continue

        # Compute weighted z-scores
        detail: dict[str, dict] = {}
        weighted_sum = 0.0
        weight_total = 0.0

        for key, vel in entity_vels.items():
            mu, sigma = pop_stats.get(key, (0.0, 0.0))
            if sigma > 0:
                z = (vel - mu) / sigma
                # Cap z-score
                z = max(-z_cap, min(z_cap, z))
            else:
                z = 0.0

            w = weights.get(key, 1.0)
            weighted_sum += z * w
            weight_total += w

            detail[key] = {
                "velocity": round(vel, 4),
                "z_score": round(z, 4),
                "weight": w,
            }

        if weight_total == 0:
            continue

        score = weighted_sum / weight_total
        acceleration = None
        if entity_id in prev_scores:
            acceleration = score - prev_scores[entity_id]

        result = {
            "entity_id": entity_id,
            "entity_name": entity.name,
            "entity_slug": entity.slug,
            "score": round(score, 4),
            "acceleration": round(acceleration, 4) if acceleration is not None else None,
            "metrics_used": len(detail),
            "detail": detail,
        }
        results.append(result)

        scores_to_create.append(
            MomentumScore(
                entity=entity,
                score=round(score, 4),
                acceleration=round(acceleration, 4) if acceleration is not None else None,
                metrics_used=len(detail),
                detail=detail,
                computed_at=now,
            )
        )

    # Sort by score descending
    results.sort(key=lambda r: r["score"], reverse=True)

    if not dry_run and scores_to_create:
        MomentumScore.objects.bulk_create(scores_to_create)
        logger.info(
            "Persisted %d momentum scores (computed_at=%s).",
            len(scores_to_create),
            now.isoformat(),
        )

    return results

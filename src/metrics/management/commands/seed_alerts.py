# metrics/management/commands/seed_alerts.py
"""
Management command: seed_alerts

Creates realistic fake Alert rows so the alerts UI can be tested without
waiting for real delta detection to fire. Covers all metric sources (GitHub,
news, App Store), all severity levels, and a mix of seen/unseen alerts spread
over a configurable time window (default 90 days).

Usage:
    python manage.py seed_alerts                          # 90 alerts over 90 days
    python manage.py seed_alerts --count 40 --days 30    # 40 alerts over 30 days
    python manage.py seed_alerts --compound               # also seed compound_spike alerts
    python manage.py seed_alerts --clear                  # delete ALL alerts and exit
    python manage.py seed_alerts --clear --count 90 --compound  # clear then reseed
"""

import logging
import random
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import Entity
from metrics.models import Alert, CompoundRule

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Scenario templates: (metric_key, threshold_pct, base_value, delta_multiplier)
# delta_multiplier controls how many times above threshold the delta is:
#   < 2  → LOW, 2–5 → MEDIUM, ≥ 5 → HIGH
# ---------------------------------------------------------------------------
_GITHUB_SCENARIOS = [
    ("github_stars", 0.20, 5_000, 1.3),  # LOW  — 26% spike
    ("github_stars", 0.20, 12_000, 2.5),  # MEDIUM — 50%
    ("github_stars", 0.20, 800, 6.0),  # HIGH  — 120%
    ("github_forks", 0.20, 2_000, 1.5),  # LOW
    ("github_forks", 0.20, 400, 3.0),  # MEDIUM
    ("github_open_issues", 0.30, 150, 1.1),  # LOW — 33%
    ("github_open_issues", 0.30, 80, 4.0),  # MEDIUM — 120%
]

_NEWS_SCENARIOS = [
    ("news_article_count", 2.00, 5, 1.1),  # LOW  — 220% (just above 200% threshold)
    ("news_article_count", 2.00, 3, 3.0),  # MEDIUM — 600%
    ("news_article_count", 2.00, 2, 7.5),  # HIGH  — 1500%
]

_APPSTORE_SCENARIOS = [
    ("appstore_rating", 0.10, 4.2, 1.2),  # LOW  — 12%
    ("appstore_rating_count", 0.15, 12_000, 2.5),  # MEDIUM — 37.5%
    ("appstore_rating_count", 0.15, 3_000, 6.0),  # HIGH  — 90%
]


def _severity(ratio: float) -> str:
    """Compute severity tier from delta/threshold ratio."""
    if ratio < 2:
        return "LOW"
    if ratio < 5:
        return "MEDIUM"
    return "HIGH"


def _pick_days_ago(rng: random.Random, max_days: int) -> int:
    """
    Pick a days_ago value weighted toward the present.

    Distributes alerts with ~60% in the last 30 days, ~25% in 30–60 days,
    and ~15% in 60–max_days. This mirrors real alert patterns where recent
    activity is denser. Adjusts gracefully for windows shorter than 60 days.
    """
    if max_days <= 30:
        return rng.randint(0, max(max_days - 1, 0))

    if max_days <= 60:
        buckets = [(0, 30, 0.70), (30, max_days, 0.30)]
    else:
        buckets = [(0, 30, 0.60), (30, 60, 0.25), (60, max_days, 0.15)]

    weights = [b[2] for b in buckets]
    low, high, _ = rng.choices(buckets, weights=weights)[0]
    return rng.randint(low, max(high - 1, low))


def _build_metric_spike(
    entity: Entity,
    metric_key: str,
    threshold_pct: float,
    base_value: float,
    multiplier: float,
    days_ago: int,
    seen: bool,
) -> tuple[Alert, object]:
    """
    Construct a single METRIC_SPIKE Alert and its desired created_at timestamp.

    Returns (Alert instance not yet saved, datetime for created_at).
    The new_value is computed as base_value * (1 + threshold_pct * multiplier)
    so delta_pct equals threshold_pct * multiplier, placing the alert at
    exactly the intended severity tier.
    """
    delta_frac = threshold_pct * multiplier
    new_value = base_value * (1 + delta_frac)
    pct_display = f"{delta_frac * 100:.1f}%"
    sev = _severity(multiplier)

    now = timezone.now()
    created_ts = now - timedelta(days=days_ago)
    baseline_at = created_ts - timedelta(days=7)

    alert = Alert(
        entity=entity,
        alert_type=Alert.AlertType.METRIC_SPIKE,
        message=(
            f"{metric_key} moved up {pct_display} "
            f"(from {base_value:.1f} to {new_value:.1f}) over 7 days."
        ),
        metadata={
            "metric_key": metric_key,
            "old_value": base_value,
            "new_value": round(new_value, 2),
            "delta_pct": round(delta_frac * 100, 2),
            "threshold_pct": round(threshold_pct * 100, 2),
            "severity": sev,
            "baseline_captured_at": baseline_at.isoformat(),
            "latest_captured_at": created_ts.isoformat(),
        },
        seen=seen,
    )
    return alert, created_ts


def _build_compound_spike(
    entity: Entity,
    rule: "CompoundRule",
    days_ago: int,
    rng: random.Random,
) -> tuple[Alert, object]:
    """
    Construct a single COMPOUND_SPIKE Alert using an active CompoundRule.

    Returns (Alert instance not yet saved, datetime for created_at).
    Each condition gets a plausible actual_delta_pct above its threshold.
    Severity is derived from the highest individual condition ratio.
    """
    now = timezone.now()
    created_ts = now - timedelta(days=days_ago)

    triggered = []
    max_ratio = 1.0
    for cond in rule.conditions:
        # actual delta slightly above threshold — realistic breach
        ratio = rng.uniform(1.1, 4.5)
        max_ratio = max(max_ratio, ratio)
        threshold_display = round(cond["threshold_pct"] * 100, 1)
        actual_display = round(cond["threshold_pct"] * ratio * 100, 1)
        triggered.append(
            {
                "metric_key": cond["metric_key"],
                "operator": cond.get("operator", "gte"),
                "threshold_pct": threshold_display,
                "actual_delta_pct": actual_display,
            }
        )

    sev = _severity(max_ratio)
    condition_summary = ", ".join(c["metric_key"] for c in triggered)

    alert = Alert(
        entity=entity,
        alert_type=Alert.AlertType.COMPOUND_SPIKE,
        message=(
            f"Compound rule '{rule.name}' triggered: "
            f"{len(triggered)} condition(s) matched ({condition_summary})."
        ),
        metadata={
            "rule_id": rule.pk,
            "triggered_conditions": triggered,
            "severity": sev,
        },
        seen=False,
    )
    return alert, created_ts


class Command(BaseCommand):
    """Populate the database with fake Alert rows for UI testing."""

    help = (
        "Seed fake alerts for UI testing. "
        "Use --clear to remove all alerts before seeding (or alone to just clear). "
        "Use --compound to also seed compound_spike alerts."
    )

    def add_arguments(self, parser) -> None:
        """Register CLI arguments."""
        parser.add_argument(
            "--count",
            type=int,
            default=None,
            help="Number of metric_spike alerts to create (default: 90). Pass 0 to skip seeding.",
        )
        parser.add_argument(
            "--days",
            type=int,
            default=90,
            help="Time window in days to spread alerts across (default: 90).",
        )
        parser.add_argument(
            "--compound",
            action="store_true",
            help="Also seed compound_spike alerts using active CompoundRule records.",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help=(
                "Delete ALL existing alerts. Used alone it only clears; "
                "combine with --count to reseed."
            ),
        )

    def handle(self, *args, **options) -> None:
        """Execute the seed_alerts command."""
        if options["clear"]:
            deleted, _ = Alert.objects.all().delete()
            self.stdout.write(self.style.WARNING(f"Cleared {deleted} alert(s)."))
            # --clear alone → stop here; combined with other flags → continue
            if options["count"] is None and not options["compound"]:
                return

        max_days: int = max(options["days"], 1)
        # Default to 90 when seeding without an explicit --count
        count: int = options["count"] if options["count"] is not None else 90

        entities = list(Entity.objects.all())
        if not entities:
            raise CommandError("No entities in the database. Run `python manage.py seed` first.")

        github_entities = [e for e in entities if e.github_org]
        news_entities = [e for e in entities if e.news_keywords]
        appstore_entities = [e for e in entities if e.app_store_id]

        all_scenarios: list[tuple] = []
        for key, threshold_pct, base, mult in _GITHUB_SCENARIOS:
            pool = github_entities or entities
            all_scenarios.append((key, threshold_pct, base, mult, pool))
        for key, threshold_pct, base, mult in _NEWS_SCENARIOS:
            pool = news_entities or entities
            all_scenarios.append((key, threshold_pct, base, mult, pool))
        for key, threshold_pct, base, mult in _APPSTORE_SCENARIOS:
            pool = appstore_entities or entities
            all_scenarios.append((key, threshold_pct, base, mult, pool))

        rng = random.Random(42)  # fixed seed → reproducible output
        pairs: list[tuple[Alert, object]] = []  # (Alert, desired_created_at)

        for _ in range(count):
            key, threshold_pct, base, mult, pool = rng.choice(all_scenarios)
            entity = rng.choice(pool)
            days_ago = _pick_days_ago(rng, max_days)
            seen = rng.random() < 0.30  # ~30% already seen

            alert, ts = _build_metric_spike(
                entity=entity,
                metric_key=key,
                threshold_pct=threshold_pct,
                base_value=base,
                multiplier=mult,
                days_ago=days_ago,
                seen=seen,
            )
            pairs.append((alert, ts))

        alerts_only = [p[0] for p in pairs]
        created = Alert.objects.bulk_create(alerts_only)

        # Patch created_at — auto_now_add=True prevents setting in constructor,
        # so we update each record individually after bulk_create returns the PKs.
        for alert_obj, desired_ts in zip(created, [p[1] for p in pairs]):
            Alert.objects.filter(pk=alert_obj.pk).update(created_at=desired_ts)

        unseen = sum(1 for a, _ in pairs if not a.seen)
        self.stdout.write(
            self.style.SUCCESS(
                f"Created {count} metric_spike alert(s) — {unseen} unseen, {count - unseen} seen "
                f"(spread over {max_days} days)."
            )
        )

        # Seed compound_spike alerts if requested
        if options["compound"]:
            self._seed_compound_spikes(rng, entities, max_days)

        self.stdout.write("  Run `python manage.py seed_alerts --clear` to remove them.")

    def _seed_compound_spikes(self, rng: random.Random, entities: list, max_days: int) -> None:
        """
        Create COMPOUND_SPIKE alerts using active CompoundRule records.

        Aims for roughly one compound alert per 3 days across the window,
        capped at 40. Skips silently if no active rules exist.
        """
        active_rules = list(CompoundRule.objects.filter(is_active=True))
        if not active_rules:
            self.stdout.write("  [SKIP] No active compound rules — skipping compound spikes.")
            return

        target = min(max(max_days // 3, 5), 40)
        pairs: list[tuple[Alert, object]] = []

        for _ in range(target):
            rule = rng.choice(active_rules)
            entity = rng.choice(entities)
            days_ago = _pick_days_ago(rng, max_days)

            alert, ts = _build_compound_spike(entity, rule, days_ago, rng)
            pairs.append((alert, ts))

        alerts_only = [p[0] for p in pairs]
        created = Alert.objects.bulk_create(alerts_only)

        for alert_obj, desired_ts in zip(created, [p[1] for p in pairs]):
            Alert.objects.filter(pk=alert_obj.pk).update(created_at=desired_ts)

        self.stdout.write(
            self.style.SUCCESS(
                f"Created {len(created)} compound_spike alert(s) (spread over {max_days} days)."
            )
        )

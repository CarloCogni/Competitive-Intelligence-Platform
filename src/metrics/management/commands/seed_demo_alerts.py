# metrics/management/commands/seed_demo_alerts.py
"""
Management command: seed_demo_alerts

Backfills demo-quality alert data across the last 90 days so the AI
Observatory charts display convincing time-series data for a presentation.

Creates:
  - ~200 METRIC_SPIKE alerts spread over 90 days with proper severity
  - ~60 COMPOUND_SPIKE alerts spread over 60 days linked to compound rules
  - ~40 AlertSeen records with useful/noise labels (GA training signal)
  - Backfills severity into existing METRIC_SPIKE alerts that lack it

Idempotent: seeded rows carry {"seeded": true} in metadata.
Re-running skips creation if seeded rows already exist (use --clear to reset).

Usage:
    python manage.py seed_demo_alerts
    python manage.py seed_demo_alerts --clear   # remove seeded rows, then reseed
    python manage.py seed_demo_alerts --clear --no-seed  # only remove seeded rows
"""

import logging
import random
from datetime import date, datetime, timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import Entity
from metrics.models import Alert, AlertSeen, CompoundRule

logger = logging.getLogger(__name__)

User = get_user_model()

# ---------------------------------------------------------------------------
# Metric spike scenarios: (metric_key, threshold_pct, base_value, multiplier)
# multiplier < 2 → LOW, 2–5 → MEDIUM, ≥ 5 → HIGH
# ---------------------------------------------------------------------------
_SPIKE_SCENARIOS = [
    # GitHub — LOW
    ("github_stars", 0.20, 5_000, 1.3),
    ("github_stars", 0.20, 8_000, 1.5),
    ("github_forks", 0.20, 2_000, 1.4),
    ("github_open_issues", 0.30, 150, 1.1),
    ("github_contributors", 0.10, 45, 1.5),
    ("news_article_count", 2.00, 5, 1.1),
    # GitHub — MEDIUM
    ("github_stars", 0.20, 12_000, 2.5),
    ("github_forks", 0.20, 400, 3.0),
    ("github_open_issues", 0.30, 80, 4.0),
    ("news_article_count", 2.00, 3, 3.0),
    ("reddit_mention_count", 0.50, 25, 2.5),
    # GitHub — HIGH
    ("github_stars", 0.20, 800, 6.0),
    ("news_article_count", 2.00, 2, 7.5),
    ("github_forks", 0.20, 300, 5.5),
    # App Store
    ("appstore_rating", 0.10, 4.2, 1.2),
    ("appstore_rating_count", 0.15, 12_000, 2.5),
    ("appstore_rating_count", 0.15, 3_000, 6.0),
]

# Severity mapping for display and metadata
_SEVERITY_WEIGHTS = {"LOW": 55, "MEDIUM": 30, "HIGH": 15}

# Weekly distribution weights (index 0 = 13 weeks ago, index 12 = current week)
# Recency bias: more alerts toward recent weeks
_WEEK_WEIGHTS = [1, 1, 2, 2, 3, 3, 4, 5, 6, 7, 8, 9, 10]


def _severity(multiplier: float) -> str:
    """Map delta/threshold ratio to severity tier."""
    if multiplier < 2:
        return "LOW"
    if multiplier < 5:
        return "MEDIUM"
    return "HIGH"


def _random_dt_in_week(week_offset: int, rng: random.Random) -> datetime:
    """Return a random timezone-aware datetime within the given week offset from today.

    week_offset=0 → current week, week_offset=12 → 13 weeks ago.
    """
    today = date.today()
    # Start of the target week (Monday)
    days_back = week_offset * 7 + rng.randint(0, 6)
    target_date = today - timedelta(days=days_back)
    hour = rng.randint(0, 23)
    minute = rng.randint(0, 59)
    dt = datetime.combine(target_date, datetime.min.time().replace(hour=hour, minute=minute))
    return timezone.make_aware(dt)


def _pick_week(rng: random.Random) -> int:
    """Pick a week offset (0=current, 12=13 weeks ago) using recency-weighted distribution."""
    population = list(range(13))
    return rng.choices(population, weights=list(reversed(_WEEK_WEIGHTS)), k=1)[0]


def _build_spike_alert(
    entity: Entity,
    metric_key: str,
    threshold_pct: float,
    base_value: float,
    multiplier: float,
    created_at: datetime,
) -> Alert:
    """Construct a single METRIC_SPIKE Alert (not yet saved)."""
    delta_frac = threshold_pct * multiplier
    new_value = base_value * (1 + delta_frac)
    pct_display = f"{delta_frac * 100:.1f}%"
    sev = _severity(multiplier)
    baseline_at = created_at - timedelta(days=7)

    return Alert(
        entity=entity,
        alert_type=Alert.AlertType.METRIC_SPIKE,
        message=(
            f"{metric_key} spiked {pct_display} "
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
            "latest_captured_at": created_at.isoformat(),
            "seeded": True,
        },
        seen=False,
    )


def _build_compound_alert(
    entity: Entity,
    rule: CompoundRule,
    created_at: datetime,
) -> Alert:
    """Construct a single COMPOUND_SPIKE Alert (not yet saved)."""
    condition_summary = " + ".join(
        c["metric_key"].replace("_", " ").title() for c in (rule.conditions or [])[:2]
    )
    return Alert(
        entity=entity,
        alert_type=Alert.AlertType.COMPOUND_SPIKE,
        message=(
            f"Compound rule '{rule.name}' triggered on {entity.name}: "
            f"{condition_summary} pattern detected."
        ),
        metadata={
            "rule_id": rule.pk,
            "rule_name": rule.name,
            "fitness_score": rule.fitness_score,
            "conditions": rule.conditions,
            "seeded": True,
        },
        seen=False,
    )


def _backfill_severity(stdout, style) -> int:
    """Add severity to existing METRIC_SPIKE alerts that lack it. Returns patch count."""
    no_sev = Alert.objects.filter(
        alert_type=Alert.AlertType.METRIC_SPIKE,
        metadata__severity=None,
    )
    # Also catch alerts where severity key is absent entirely
    # JSONField lookup for missing key: use raw iteration for safety
    patched = 0
    for alert in Alert.objects.filter(alert_type=Alert.AlertType.METRIC_SPIKE):
        if isinstance(alert.metadata, dict) and "severity" not in alert.metadata:
            # Assign LOW by default (safe, conservative)
            alert.metadata["severity"] = "LOW"
            Alert.objects.filter(pk=alert.pk).update(metadata=alert.metadata)
            patched += 1
    return patched


class Command(BaseCommand):
    """Seed demo-quality alert data for AI Observatory charts."""

    help = (
        "Backfill 90 days of METRIC_SPIKE and COMPOUND_SPIKE alerts for demo presentation. "
        "Also seeds AlertSeen labels for GA training chart."
    )

    def add_arguments(self, parser) -> None:
        """Define command-line arguments."""
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Remove all rows with metadata.seeded=true before reseeding.",
        )
        parser.add_argument(
            "--no-seed",
            action="store_true",
            help="When combined with --clear, only removes seeded rows without creating new ones.",
        )
        parser.add_argument(
            "--spike-count",
            type=int,
            default=200,
            help="Number of METRIC_SPIKE alerts to create (default: 200).",
        )
        parser.add_argument(
            "--compound-count",
            type=int,
            default=60,
            help="Number of COMPOUND_SPIKE alerts to create (default: 60).",
        )
        parser.add_argument(
            "--seen-count",
            type=int,
            default=40,
            help="Number of AlertSeen label records to create (default: 40).",
        )

    def handle(self, *args, **options) -> None:
        """Execute seed_demo_alerts."""
        rng = random.Random(2026)

        if options["clear"]:
            # Delete all seeded alerts (the AlertSeen rows cascade automatically)
            deleted_alerts = 0
            for alert in Alert.objects.all():
                if isinstance(alert.metadata, dict) and alert.metadata.get("seeded"):
                    alert.delete()
                    deleted_alerts += 1
            self.stdout.write(self.style.WARNING(f"Removed {deleted_alerts} seeded alert(s)."))
            if options["no_seed"]:
                return

        # Check idempotency guard
        seeded_exists = any(
            isinstance(a.metadata, dict) and a.metadata.get("seeded")
            for a in Alert.objects.all()[:500]
        )
        if seeded_exists and not options["clear"]:
            self.stdout.write(
                self.style.WARNING("Seeded alerts already exist. Use --clear to remove and reseed.")
            )
            return

        entities = list(Entity.objects.all())
        if not entities:
            raise CommandError("No entities found. Run `python manage.py seed` first.")

        github_entities = [e for e in entities if e.github_org]
        appstore_entities = [e for e in entities if e.app_store_id]

        # --- METRIC_SPIKE alerts ---
        spike_count: int = options["spike_count"]
        spike_alerts: list[Alert] = []
        for _ in range(spike_count):
            scenario = rng.choice(_SPIKE_SCENARIOS)
            metric_key, threshold_pct, base_value, multiplier = scenario

            # Pick entity pool by metric type
            if "appstore" in metric_key:
                pool = appstore_entities or entities
            elif "github" in metric_key or "reddit" in metric_key or "news" in metric_key:
                pool = github_entities or entities
            else:
                pool = entities

            entity = rng.choice(pool)
            week_offset = _pick_week(rng)
            created_at = _random_dt_in_week(week_offset, rng)

            spike_alerts.append(
                _build_spike_alert(
                    entity, metric_key, threshold_pct, base_value, multiplier, created_at
                )
            )

        created_spikes = Alert.objects.bulk_create(spike_alerts)

        # Backdate created_at (auto_now_add sets all to now after bulk_create)
        for alert_obj, spike in zip(created_spikes, spike_alerts):
            Alert.objects.filter(pk=alert_obj.pk).update(
                created_at=spike.metadata["latest_captured_at"]
            )

        self.stdout.write(self.style.SUCCESS(f"Created {len(created_spikes)} METRIC_SPIKE alerts."))

        # --- COMPOUND_SPIKE alerts ---
        compound_count: int = options["compound_count"]
        active_rules = list(CompoundRule.objects.filter(is_active=True))
        if not active_rules:
            self.stdout.write(
                self.style.WARNING(
                    "No active compound rules found — skipping COMPOUND_SPIKE seeding."
                )
            )
            compound_alerts_created = 0
        else:
            compound_alerts: list[Alert] = []
            compound_created_ats: list[datetime] = []

            for _ in range(compound_count):
                rule = rng.choice(active_rules)
                entity = rng.choice(entities)
                # Compound rules exist from Dec 2025, so limit to last 60 days
                days_ago = rng.randint(0, 59)
                today = date.today()
                target_date = today - timedelta(days=days_ago)
                dt = datetime.combine(
                    target_date,
                    datetime.min.time().replace(hour=rng.randint(0, 23), minute=rng.randint(0, 59)),
                )
                created_at = timezone.make_aware(dt)
                compound_alerts.append(_build_compound_alert(entity, rule, created_at))
                compound_created_ats.append(created_at)

            created_compounds = Alert.objects.bulk_create(compound_alerts)
            for alert_obj, created_at in zip(created_compounds, compound_created_ats):
                Alert.objects.filter(pk=alert_obj.pk).update(created_at=created_at)

            compound_alerts_created = len(created_compounds)
            self.stdout.write(
                self.style.SUCCESS(f"Created {compound_alerts_created} COMPOUND_SPIKE alerts.")
            )

        # --- AlertSeen labels ---
        seen_count: int = options["seen_count"]
        admin_user = User.objects.filter(is_superuser=True).first()
        if not admin_user:
            self.stdout.write(
                self.style.WARNING("No superuser found — skipping AlertSeen seeding.")
            )
        else:
            # Pick from all spike + compound alerts we just created
            all_seeded_pks = [a.pk for a in created_spikes]
            if active_rules and compound_alerts_created > 0:
                all_seeded_pks += [a.pk for a in created_compounds]

            seen_alerts = rng.sample(all_seeded_pks, min(seen_count, len(all_seeded_pks)))
            seen_records_created = 0
            for pk in seen_alerts:
                # useful distribution: 60% True, 30% False, 10% None
                roll = rng.random()
                if roll < 0.60:
                    useful: bool | None = True
                elif roll < 0.90:
                    useful = False
                else:
                    useful = None

                _, created = AlertSeen.objects.get_or_create(
                    user=admin_user,
                    alert_id=pk,
                    defaults={"useful": useful},
                )
                if created:
                    seen_records_created += 1

            self.stdout.write(
                self.style.SUCCESS(f"Created {seen_records_created} AlertSeen label records.")
            )

        # --- Backfill severity on existing alerts ---
        patched = _backfill_severity(self.stdout, self.style)
        if patched:
            self.stdout.write(
                self.style.SUCCESS(f"Backfilled severity on {patched} existing alerts.")
            )

        self.stdout.write(
            self.style.SUCCESS("Done. AI Observatory charts should now show 90-day data.")
        )

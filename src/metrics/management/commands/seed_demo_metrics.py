# metrics/management/commands/seed_demo_metrics.py
"""
Management command: seed_demo_metrics

Backfills 90 days of synthetic Metric rows for key entities so that
sparkline charts on the entity detail pages show meaningful trends.

Targets the top Blockstream entities and their main competitors across
four metric keys with realistic growth curves + small variance.

Idempotent: seeded rows carry source="seeded". Re-running skips creation
unless --clear is passed.

Usage:
    python manage.py seed_demo_metrics
    python manage.py seed_demo_metrics --clear     # remove seeded rows, then reseed
    python manage.py seed_demo_metrics --clear --no-seed  # only remove
"""

import logging
import random
from datetime import date, datetime, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import Entity
from metrics.models import Metric

logger = logging.getLogger(__name__)

# Entities to cover (slug → starting value per metric_key)
# Values are approximate real-world baselines as of early 2026
_ENTITY_BASELINES: dict[str, dict[str, float]] = {
    "jade": {
        "github_stars": 820,
        "github_forks": 95,
        "news_article_count": 4,
    },
    "green-wallet": {
        "github_stars": 1_450,
        "github_forks": 180,
        "news_article_count": 6,
    },
    "liquid-network": {
        "github_stars": 2_300,
        "github_forks": 310,
        "news_article_count": 12,
    },
    "gdk": {
        "github_stars": 640,
        "github_forks": 88,
        "news_article_count": 3,
    },
    "blockstream-custody": {
        "github_stars": 0,
        "github_forks": 0,
        "news_article_count": 8,
    },
    "coldcard": {
        "github_stars": 1_100,
        "github_forks": 130,
        "news_article_count": 7,
    },
    "ledger-flex": {
        "github_stars": 3_800,
        "github_forks": 490,
        "news_article_count": 18,
    },
    "trezor-model-t": {
        "github_stars": 2_900,
        "github_forks": 370,
        "news_article_count": 14,
    },
    "bitgo": {
        "github_stars": 0,
        "github_forks": 0,
        "news_article_count": 15,
    },
    "fireblocks": {
        "github_stars": 0,
        "github_forks": 0,
        "news_article_count": 22,
    },
}

# Growth rates per metric key (fraction per day)
_GROWTH_RATES: dict[str, float] = {
    "github_stars": 0.0015,  # ~0.15% per day → ~14% over 90 days
    "github_forks": 0.0012,
    "news_article_count": 0.0,  # news is noisy, not trending; use variance only
}

# Daily variance (fraction of current value, +/-)
_VARIANCE: dict[str, float] = {
    "github_stars": 0.008,
    "github_forks": 0.010,
    "news_article_count": 0.40,  # news counts are highly variable
}

_SEEDED_SOURCE = "seeded"


def _generate_series(
    start_value: float,
    growth_rate: float,
    variance: float,
    days: int,
    rng: random.Random,
) -> list[float]:
    """Generate a synthetic time series with trend + noise.

    Uses a simple multiplicative random walk overlaid on an exponential trend.
    Values are clipped to >= 0.
    """
    values: list[float] = []
    current = start_value
    for d in range(days):
        trend = current * (1 + growth_rate)
        noise = current * variance * (rng.random() * 2 - 1)
        current = max(0.0, trend + noise)
        # For integer metrics (stars, forks) round to whole numbers
        values.append(round(current, 2))
    return values


class Command(BaseCommand):
    """Backfill 90 days of synthetic metric data for sparkline charts."""

    help = (
        "Seed 90 days of synthetic Metric rows for key entities. "
        "Gives sparkline charts on the entity detail pages meaningful trend data."
    )

    def add_arguments(self, parser) -> None:
        """Define command-line arguments."""
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Remove all rows with source='seeded' before reseeding.",
        )
        parser.add_argument(
            "--no-seed",
            action="store_true",
            help="When combined with --clear, only removes seeded rows.",
        )
        parser.add_argument(
            "--days",
            type=int,
            default=90,
            help="Number of days to backfill (default: 90).",
        )

    def handle(self, *args, **options) -> None:
        """Execute seed_demo_metrics."""
        rng = random.Random(2026)

        if options["clear"]:
            deleted, _ = Metric.objects.filter(source=_SEEDED_SOURCE).delete()
            self.stdout.write(self.style.WARNING(f"Removed {deleted} seeded metric row(s)."))
            if options["no_seed"]:
                return

        # Idempotency guard
        if Metric.objects.filter(source=_SEEDED_SOURCE).exists():
            self.stdout.write(
                self.style.WARNING(
                    "Seeded metrics already exist. Use --clear to remove and reseed."
                )
            )
            return

        days: int = options["days"]
        today = date.today()
        all_entities = {e.slug: e for e in Entity.objects.all()}

        rows_to_create: list[Metric] = []
        entities_covered = 0

        for slug, baselines in _ENTITY_BASELINES.items():
            entity = all_entities.get(slug)
            if not entity:
                self.stdout.write(self.style.WARNING(f"Entity '{slug}' not found — skipping."))
                continue

            entities_covered += 1
            for metric_key, start_value in baselines.items():
                # Skip metrics with zero baseline (no meaningful data to show)
                if start_value == 0:
                    continue

                growth = _GROWTH_RATES[metric_key]
                variance = _VARIANCE[metric_key]
                series = _generate_series(start_value, growth, variance, days, rng)

                for day_offset, value in enumerate(series):
                    # Article counts must be whole numbers >= 1 to avoid near-zero
                    # baselines that inflate percentage calculations in Top Movers
                    if metric_key == "news_article_count":
                        value = max(1.0, round(value))
                    target_date = today - timedelta(days=days - 1 - day_offset)
                    # One data point per day at a consistent hour (avoids clutter)
                    hour = 14  # 2pm UTC — typical scheduler run time
                    dt = datetime.combine(target_date, datetime.min.time().replace(hour=hour))
                    captured_at = timezone.make_aware(dt)

                    rows_to_create.append(
                        Metric(
                            entity=entity,
                            source=_SEEDED_SOURCE,
                            key=metric_key,
                            value=value,
                            captured_at=captured_at,
                        )
                    )

        if not rows_to_create:
            raise CommandError("No rows to create — check entity slugs in _ENTITY_BASELINES.")

        # Bulk insert in batches to avoid SQLite limits
        batch_size = 500
        total_created = 0
        for i in range(0, len(rows_to_create), batch_size):
            batch = rows_to_create[i : i + batch_size]
            Metric.objects.bulk_create(batch)
            total_created += len(batch)

        self.stdout.write(
            self.style.SUCCESS(
                f"Created {total_created} seeded metric rows across "
                f"{entities_covered} entities and {days} days."
            )
        )
        self.stdout.write("  Run `python manage.py seed_demo_metrics --clear` to remove them.")

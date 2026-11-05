# metrics/management/commands/seed_momentum.py
"""
Seed 90 days of synthetic momentum score history for demo/development.

Generates realistic momentum trajectories for all tracked entities:
  - Primary (Blockstream) entities get a slight upward bias
  - Active competitors get neutral random walks
  - Sparse competitors get slightly negative drift

Each entity's trajectory is a smoothed random walk with auto-correlated noise,
producing believable acceleration patterns.

Command is idempotent.  Use ``--clear`` to wipe and reseed.

Usage::

    python manage.py seed_momentum
    python manage.py seed_momentum --clear
    python manage.py seed_momentum --days 60
"""

import hashlib
import logging
import random
from datetime import datetime, timedelta, timezone

from django.core.management.base import BaseCommand

from core.models import Entity
from metrics.models import MomentumScore

logger = logging.getLogger(__name__)

# Number of days of history to generate
DEFAULT_DAYS = 90

# Metric key subsets per entity capability (for realistic metrics_used counts)
METRIC_FIELD_MAP: dict[str, list[str]] = {
    "github_org": [
        "github_stars",
        "github_forks",
        "github_open_issues",
        "github_public_repos",
        "github_contributors",
        "github_latest_release_days_ago",
    ],
    "app_store_id": ["appstore_rating", "appstore_rating_count"],
    "play_store_id": ["playstore_rating", "playstore_rating_count"],
    "twitter_handle": [
        "twitter_mention_count",
        "twitter_total_engagement",
        "twitter_avg_engagement",
    ],
}


def _entity_seed(entity_slug: str) -> int:
    """Deterministic seed per entity for reproducible random walks."""
    return int(hashlib.md5(entity_slug.encode()).hexdigest()[:8], 16)


def _count_metrics(entity: Entity) -> int:
    """Count how many metric keys this entity would have based on its config."""
    count = 0
    for field, keys in METRIC_FIELD_MAP.items():
        val = getattr(entity, field, None)
        if val:
            count += len(keys)
    # News and reddit are always present if keywords configured
    if getattr(entity, "news_keywords", None):
        count += 1
    if getattr(entity, "reddit_keywords", None):
        count += 1
    return max(count, 1)


class Command(BaseCommand):
    help = "Seed synthetic momentum score history for demo purposes."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Delete all existing MomentumScore rows before seeding.",
        )
        parser.add_argument(
            "--days",
            type=int,
            default=DEFAULT_DAYS,
            help=f"Number of days of history to generate (default: {DEFAULT_DAYS}).",
        )

    def handle(self, *args, **options) -> None:
        if options["clear"]:
            deleted, _ = MomentumScore.objects.all().delete()
            self.stdout.write(self.style.WARNING(f"Cleared {deleted} existing momentum scores."))

        days = options["days"]
        entities = list(Entity.objects.all())
        if not entities:
            self.stdout.write(self.style.WARNING("No entities found."))
            return

        now = datetime.now(tz=timezone.utc)
        start = now - timedelta(days=days)

        scores_to_create: list[MomentumScore] = []

        for entity in entities:
            rng = random.Random(_entity_seed(entity.slug))
            metrics_used = _count_metrics(entity)

            # Determine drift based on entity type
            if entity.is_primary:
                drift = 0.008  # slight upward bias for Blockstream entities
            elif metrics_used >= 6:
                drift = 0.0  # neutral for well-covered competitors
            else:
                drift = -0.005  # slight downward for sparse entities

            # Generate random walk with momentum (auto-correlated)
            score = rng.gauss(0, 0.3)  # initial score
            velocity = 0.0  # smoothing term

            for day_offset in range(days):
                # Random walk with mean reversion and drift
                noise = rng.gauss(0, 0.08)
                mean_reversion = -0.03 * score  # pull toward zero
                velocity = 0.7 * velocity + 0.3 * (noise + drift + mean_reversion)
                score += velocity

                # Clamp to reasonable range
                score = max(-2.5, min(2.5, score))

                # Build synthetic detail (simplified — just a few keys with plausible z-scores)
                detail: dict[str, dict] = {}
                remaining = score
                for field, keys in METRIC_FIELD_MAP.items():
                    val = getattr(entity, field, None)
                    if not val:
                        continue
                    for key in keys:
                        z = rng.gauss(remaining / max(metrics_used, 1), 0.4)
                        z = max(-3.0, min(3.0, z))
                        detail[key] = {
                            "velocity": round(z * rng.uniform(10, 500), 4),
                            "z_score": round(z, 4),
                            "weight": 1.0,
                        }

                computed_at = start + timedelta(days=day_offset, hours=4)

                # Acceleration from previous score
                prev_score = (
                    scores_to_create[-1].score
                    if scores_to_create and scores_to_create[-1].entity == entity
                    else None
                )
                acceleration = round(score - prev_score, 4) if prev_score is not None else None

                scores_to_create.append(
                    MomentumScore(
                        entity=entity,
                        score=round(score, 4),
                        acceleration=acceleration,
                        metrics_used=metrics_used,
                        detail=detail,
                        computed_at=computed_at,
                    )
                )

        MomentumScore.objects.bulk_create(scores_to_create, batch_size=500)
        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {len(scores_to_create)} momentum scores "
                f"({len(entities)} entities x {days} days)."
            )
        )

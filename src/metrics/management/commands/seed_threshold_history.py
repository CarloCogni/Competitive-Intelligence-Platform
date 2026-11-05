# metrics/management/commands/seed_threshold_history.py
"""Seed weekly ThresholdSnapshot history for ALL tracked entities.

Creates realistic cold_start → dynamic convergence arcs for every entity
in the database, assigning metric keys based on each entity's config
(github_org, app_store_id, twitter_handle, etc.).

Three entity archetypes control convergence speed:
  - Primary (Blockstream): fast convergence, 1 cold_start point
  - Active competitor (has GitHub + Twitter): medium convergence, 2 cold_start
  - Sparse competitor (missing GitHub or Twitter): slow convergence, 2 cold_start

Command is idempotent.  Use ``--clear`` to wipe and reseed.

Usage::

    python manage.py seed_threshold_history          # first run
    python manage.py seed_threshold_history --clear   # wipe + reseed
"""

import hashlib
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# 20 weekly snapshots: Nov 3 2025 → Mar 23 2026
START_DATE = datetime(2025, 11, 3, 12, 0, 0, tzinfo=timezone.utc)
NUM_POINTS = 20
INTERVAL_DAYS = 7

# Per-metric: (cold_start_base, final_dynamic_base)
# Values are fractions (0.20 = 20%).  Cold start is ~25% above the static
# DeltaThreshold; final dynamic converges toward the static threshold.
METRIC_RANGES: dict[str, tuple[float, float]] = {
    "github_stars": (0.255, 0.205),
    "github_forks": (0.265, 0.220),
    "github_open_issues": (0.400, 0.355),
    "github_contributors": (0.130, 0.105),
    "appstore_rating": (0.130, 0.095),
    "appstore_rating_count": (0.205, 0.155),
    "news_article_count": (2.650, 2.200),
    "reddit_mention_count": (0.655, 0.530),
    "twitter_mention_count": (0.665, 0.520),
    "twitter_avg_engagement": (0.400, 0.305),
    "twitter_total_engagement": (0.675, 0.540),
}

# Entity config field → metric keys it unlocks
METRIC_FIELD_MAP: dict[str, list[str]] = {
    "github_org": [
        "github_stars",
        "github_forks",
        "github_open_issues",
        "github_contributors",
    ],
    "app_store_id": [
        "appstore_rating",
        "appstore_rating_count",
    ],
    "news_keywords": [
        "news_article_count",
    ],
    "reddit_keywords": [
        "reddit_mention_count",
    ],
    "twitter_handle": [
        "twitter_mention_count",
        "twitter_avg_engagement",
        "twitter_total_engagement",
    ],
}


# ---------------------------------------------------------------------------
# Entity archetype profiles
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EntityProfile:
    """Controls convergence behaviour for an entity archetype."""

    decay_rate: float
    base_offset: float
    cold_start_points: int
    jitter_amplitude: float


PROFILE_PRIMARY = EntityProfile(
    decay_rate=1.0,
    base_offset=-0.010,
    cold_start_points=1,
    jitter_amplitude=0.008,
)

PROFILE_ACTIVE = EntityProfile(
    decay_rate=0.6,
    base_offset=0.005,
    cold_start_points=2,
    jitter_amplitude=0.012,
)

PROFILE_SPARSE = EntityProfile(
    decay_rate=0.35,
    base_offset=0.015,
    cold_start_points=2,
    jitter_amplitude=0.018,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash_float(key: str, lo: float, hi: float) -> float:
    """Return a deterministic float in [lo, hi] derived from *key*."""
    digest = int(hashlib.md5(key.encode()).hexdigest(), 16)
    return lo + (digest % 10_000) / 10_000.0 * (hi - lo)


def _entity_offset(entity_id: int, metric_key: str) -> float:
    """Constant per-(entity, metric) shift so curves don't stack perfectly."""
    return _hash_float(f"off:{entity_id}:{metric_key}", -0.008, 0.008)


def _point_jitter(entity_id: int, metric_key: str, idx: int, amplitude: float) -> float:
    """Per-data-point noise, scaled by the archetype's jitter amplitude."""
    return _hash_float(f"jit:{entity_id}:{metric_key}:{idx}", -amplitude, amplitude)


def _get_profile(entity: "Entity") -> EntityProfile:  # noqa: F821
    """Select the convergence profile for *entity*."""
    if entity.is_primary:
        return PROFILE_PRIMARY
    has_github = bool(entity.github_org)
    has_twitter = bool(entity.twitter_handle)
    if has_github and has_twitter:
        return PROFILE_ACTIVE
    return PROFILE_SPARSE


def _metrics_for_entity(entity: "Entity") -> list[str]:  # noqa: F821
    """Return the metric keys that apply to *entity* based on its config."""
    keys: list[str] = []
    for field, metrics in METRIC_FIELD_MAP.items():
        value = getattr(entity, field, None)
        # JSONField lists are truthy when non-empty; CharFields when non-blank
        if value:
            keys.extend(metrics)
    return keys


def _generate_dates() -> list[datetime]:
    """Return NUM_POINTS weekly dates starting from START_DATE."""
    return [START_DATE + timedelta(days=i * INTERVAL_DAYS) for i in range(NUM_POINTS)]


def _compute_value(
    cold_base: float,
    final_base: float,
    entity_id: int,
    metric_key: str,
    profile: EntityProfile,
    idx: int,
) -> tuple[float, str]:
    """Compute (threshold_value, source) for a single data point.

    Returns the effective threshold (fractional) and its source label.
    Cold start values appear only at indices < profile.cold_start_points;
    all subsequent points are dynamic with exponential-decay convergence.
    """
    ent_off = _entity_offset(entity_id, metric_key)
    cold_val = cold_base + ent_off + profile.base_offset
    final_val = final_base + ent_off + profile.base_offset

    if idx < profile.cold_start_points:
        source = "cold_start"
        base = cold_val
    else:
        source = "dynamic"
        dynamic_idx = idx - profile.cold_start_points
        dynamic_total = NUM_POINTS - profile.cold_start_points - 1
        t = dynamic_idx / max(dynamic_total, 1)
        base = final_val + (cold_val - final_val) * math.exp(-profile.decay_rate * t * 5)

    jitter = _point_jitter(entity_id, metric_key, idx, profile.jitter_amplitude)
    value = max(round(base + jitter, 6), 0.005)
    return value, source


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


class Command(BaseCommand):
    """Seed weekly ThresholdSnapshot history for all tracked entities."""

    help = (
        "Seed demo ThresholdSnapshot history for all entities.  "
        "Use --clear to wipe existing data first."
    )

    def add_arguments(self, parser: "ArgumentParser") -> None:  # noqa: F821
        """Register the --clear flag."""
        parser.add_argument(
            "--clear",
            action="store_true",
            default=False,
            help="Delete ALL ThresholdSnapshot rows before seeding.",
        )

    def handle(self, *args: object, **options: object) -> None:
        """Create ThresholdSnapshot rows for every entity × applicable metric."""
        from core.models import Entity
        from metrics.models import ThresholdSnapshot

        # --clear: wipe existing data
        if options["clear"]:
            deleted, _ = ThresholdSnapshot.objects.all().delete()
            self.stdout.write(
                self.style.WARNING(f"  Removed {deleted} existing threshold snapshot(s).")
            )

        # Idempotency guard (skip if data already exists and --clear was not used)
        if ThresholdSnapshot.objects.filter(source__in=["cold_start", "dynamic"]).exists():
            count = ThresholdSnapshot.objects.count()
            self.stdout.write(
                f"  [SKIP] {count} threshold snapshots already exist. "
                f"Use --clear to wipe and reseed."
            )
            return

        dates = _generate_dates()
        entities = list(Entity.objects.order_by("name"))
        if not entities:
            self.stdout.write("  [SKIP] No entities in database.")
            return

        batch: list[ThresholdSnapshot] = []
        entity_count = 0

        for entity in entities:
            metrics = _metrics_for_entity(entity)
            if not metrics:
                self.stdout.write(f"  skip: {entity.name} (no applicable metrics)")
                continue

            profile = _get_profile(entity)
            entity_count += 1

            for metric_key in metrics:
                cold_base, final_base = METRIC_RANGES[metric_key]
                for idx, dt in enumerate(dates):
                    value, source = _compute_value(
                        cold_base, final_base, entity.id, metric_key, profile, idx
                    )
                    batch.append(
                        ThresholdSnapshot(
                            entity=entity,
                            metric_key=metric_key,
                            effective_threshold=value,
                            source=source,
                            captured_at=dt,
                        )
                    )

        # Bulk insert in batches of 500 (SQLite friendly)
        ThresholdSnapshot.objects.bulk_create(batch, batch_size=500)

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. Created {len(batch)} snapshots for {entity_count} entities."
            )
        )
        logger.info(
            "seed_threshold_history: created=%d entities=%d",
            len(batch),
            entity_count,
        )

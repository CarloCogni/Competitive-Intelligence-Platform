# metrics/management/commands/train_anomaly_models.py
"""
Management command: train_anomaly_models

Trains Isolation Forest anomaly detection models for all (entity, metric_key)
pairs where DeltaThreshold.use_dynamic_threshold is True.

For entities with sufficient history (>= min_points pct_change samples),
trains a full Isolation Forest model. For sparse entities, optionally
uses an LLM to suggest a cold-start threshold.

An optional LLM review gate checks data quality before training (can be
bypassed with --skip-llm-review for testing/seeding).

Usage:
    python manage.py train_anomaly_models
    python manage.py train_anomaly_models --min-points 20
    python manage.py train_anomaly_models --entity blockstream --metric github_stars
    python manage.py train_anomaly_models --skip-llm-review
    python manage.py train_anomaly_models --use-groq
    python manage.py train_anomaly_models --cleanup
"""

import logging

import joblib
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Entity
from metrics.anomaly import AnomalyDetector, compute_pct_changes
from metrics.models import DeltaThreshold, Metric, ThresholdSnapshot

logger = logging.getLogger(__name__)

# Maps each metric key to the Entity field that must be non-empty for the
# entity to be capable of producing that metric. Entities that lack the
# required field will never have real data ingested, so training a model
# for them would only create a spurious cold-start artifact.
METRIC_SOURCE_FIELDS: dict[str, str] = {
    "appstore_rating": "app_store_id",
    "appstore_rating_count": "app_store_id",
    "github_stars": "github_repo",
    "github_forks": "github_repo",
    "github_open_issues": "github_repo",
    "github_contributors": "github_repo",
    "twitter_mention_count": "twitter_handle",
    "twitter_avg_engagement": "twitter_handle",
    "twitter_total_engagement": "twitter_handle",
    "reddit_mention_count": "reddit_keywords",
    "news_article_count": "news_keywords",
}

# Ordered list of source fields fetched alongside id/slug/vertical in values_list.
# The index here must stay in sync with _FIELD_INDEX below.
_SOURCE_FIELDS = (
    "app_store_id",
    "github_repo",
    "twitter_handle",
    "reddit_keywords",
    "news_keywords",
)
_FIELD_INDEX: dict[str, int] = {name: idx + 3 for idx, name in enumerate(_SOURCE_FIELDS)}


def _entity_can_produce(entity_row: tuple, metric_key: str) -> bool:
    """Return True if the entity has the required source field for this metric.

    Entities without the relevant API identifier (e.g. no app_store_id) will
    never have data ingested for that metric, so training is pointless.
    Unknown metric keys (not in METRIC_SOURCE_FIELDS) are allowed through.
    """
    required_field = METRIC_SOURCE_FIELDS.get(metric_key)
    if required_field is None:
        return True

    value = entity_row[_FIELD_INDEX[required_field]]
    # JSONFields (list) — check for non-empty list; CharFields — check non-empty string
    if isinstance(value, list):
        return len(value) > 0
    return bool(value)


class Command(BaseCommand):
    help = (
        "Train Isolation Forest anomaly models for all (entity, metric_key) pairs "
        "where DeltaThreshold.use_dynamic_threshold=True."
    )

    def add_arguments(self, parser) -> None:
        """Define CLI arguments."""
        parser.add_argument(
            "--entity",
            dest="entity_slug",
            default=None,
            metavar="SLUG",
            help="Limit training to a single entity by slug.",
        )
        parser.add_argument(
            "--metric",
            dest="metric_key",
            default=None,
            metavar="KEY",
            help="Limit training to a single metric key (e.g. 'github_stars').",
        )
        parser.add_argument(
            "--min-points",
            dest="min_points",
            type=int,
            default=30,
            help="Minimum pct_change data points required to train (default: 30).",
        )
        parser.add_argument(
            "--skip-llm-review",
            action="store_true",
            default=False,
            help="Bypass LLM data quality review and cold-start suggestions.",
        )
        parser.add_argument(
            "--use-groq",
            action="store_true",
            default=False,
            help="Force Groq backend even if Ollama is available.",
        )
        parser.add_argument(
            "--cleanup",
            action="store_true",
            default=False,
            help=(
                "Delete .joblib files for entity/metric pairs where the entity "
                "lacks the required source field. Runs before training."
            ),
        )

    def handle(self, *args, **options) -> None:
        """Train anomaly models for all qualifying (entity, metric_key) pairs."""
        entity_slug = options["entity_slug"]
        metric_key_filter = options["metric_key"]
        min_points = options["min_points"]
        skip_llm = options["skip_llm_review"]
        use_groq = options["use_groq"]
        do_cleanup = options["cleanup"]

        # Load thresholds with dynamic detection enabled
        thresholds_qs = DeltaThreshold.objects.filter(use_dynamic_threshold=True)
        if metric_key_filter:
            thresholds_qs = thresholds_qs.filter(metric_key=metric_key_filter)

        thresholds = list(thresholds_qs)
        if not thresholds:
            self.stdout.write(
                self.style.WARNING("No DeltaThreshold rows have use_dynamic_threshold=True.")
            )
            return

        # Load entities, including source fields needed for eligibility checks
        entities_qs = Entity.objects.all()
        if entity_slug:
            entities_qs = entities_qs.filter(slug=entity_slug)
        entities = list(entities_qs.values_list("id", "slug", "vertical", *_SOURCE_FIELDS))

        if not entities:
            self.stdout.write(self.style.WARNING("No entities found."))
            return

        model_dir = settings.ANOMALY_MODEL_DIR

        # Remove stale model files for pairs the entity can't produce data for
        if do_cleanup:
            self._cleanup_ineligible_models(entities, thresholds, model_dir)

        # Set up LLM reviewer if enabled
        reviewer = None
        if not skip_llm:
            reviewer = self._build_reviewer(use_groq)

        trained = 0
        cold_start = 0
        skipped = 0
        ineligible = 0
        llm_skipped = 0

        for entity_row in entities:
            entity_id, slug, vertical = entity_row[0], entity_row[1], entity_row[2]
            for dt in thresholds:
                # Skip pairs where the entity can never produce this metric
                if not _entity_can_produce(entity_row, dt.metric_key):
                    ineligible += 1
                    self.stdout.write(
                        f"  [INELIGIBLE]  entity={slug!r:30s} "
                        f"key={dt.metric_key!r:30s} (no source field)"
                    )
                    continue

                # Fetch historical values, oldest-first
                values = list(
                    Metric.objects.filter(entity_id=entity_id, key=dt.metric_key)
                    .exclude(value__isnull=True)
                    .order_by("captured_at")
                    .values_list("value", flat=True)
                )

                pct_changes = compute_pct_changes(values)

                # Compute time span for LLM context
                span_days = self._compute_span_days(entity_id, dt.metric_key)

                detector = AnomalyDetector(
                    model_dir=model_dir,
                    entity_id=entity_id,
                    metric_key=dt.metric_key,
                )

                if len(pct_changes) >= min_points:
                    # Sufficient data — train full model
                    ok = detector.train(
                        pct_changes,
                        min_points=min_points,
                        reviewer=reviewer,
                        entity_slug=slug,
                        span_days=span_days,
                    )
                    if ok:
                        trained += 1
                        self.stdout.write(
                            f"  [OK]          entity={slug!r:30s} "
                            f"key={dt.metric_key!r:30s} n={len(pct_changes)}"
                        )
                    else:
                        # LLM vetoed training
                        llm_skipped += 1
                        self.stdout.write(
                            self.style.WARNING(
                                f"  [LLM-SKIP]    entity={slug!r:30s} "
                                f"key={dt.metric_key!r:30s} n={len(pct_changes)} "
                                f"(LLM review vetoed)"
                            )
                        )
                else:
                    # Sparse data — try LLM cold-start
                    if reviewer is not None:
                        suggestion = reviewer.suggest_cold_start_threshold(
                            entity_slug=slug,
                            metric_key=dt.metric_key,
                            vertical=vertical or "Unknown",
                            n_available=len(pct_changes),
                            min_points=min_points,
                        )
                        if suggestion is not None:
                            detector.save_cold_start(
                                threshold_pct=suggestion.threshold_pct,
                                reason=suggestion.reason,
                            )
                            cold_start += 1
                            self.stdout.write(
                                f"  [COLD-START]  entity={slug!r:30s} "
                                f"key={dt.metric_key!r:30s} "
                                f"suggested={suggestion.threshold_pct * 100:.0f}% "
                                f"({suggestion.reason})"
                            )
                            continue

                    # No LLM or suggestion failed — skip
                    skipped += 1
                    self.stdout.write(
                        self.style.WARNING(
                            f"  [SKIP]        entity={slug!r:30s} "
                            f"key={dt.metric_key!r:30s} n={len(pct_changes)} "
                            f"(need {min_points})"
                        )
                    )

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. trained={trained}, cold-start={cold_start}, "
                f"skipped={skipped}, ineligible={ineligible}, llm-skipped={llm_skipped}."
            )
        )

        # Record threshold snapshots for evolution tracking
        self._record_threshold_snapshots(model_dir)

    def _cleanup_ineligible_models(self, entities: list, thresholds: list, model_dir) -> None:
        """Delete .joblib files for entity/metric pairs the entity can't produce data for."""
        removed = 0
        for entity_row in entities:
            entity_id, slug = entity_row[0], entity_row[1]
            for dt in thresholds:
                if not _entity_can_produce(entity_row, dt.metric_key):
                    path = model_dir / f"{entity_id}_{dt.metric_key}.joblib"
                    if path.exists():
                        path.unlink()
                        removed += 1
                        self.stdout.write(f"  [REMOVED]     {path.name}  (entity={slug!r})")
        self.stdout.write(
            self.style.SUCCESS(f"Cleanup done. Removed {removed} ineligible model file(s).\n")
        )

    def _build_reviewer(self, use_groq: bool):
        """Build the LLM reviewer, handling import and configuration."""
        from metrics.llm_review import AnomalyLLMReviewer, LLMClient

        if use_groq:
            client = LLMClient(
                groq_api_key=settings.GROQ_API_KEY,
                groq_model=settings.GROQ_MODEL,
                ollama_base_url="http://unreachable:0",
            )
        else:
            client = LLMClient.from_settings()

        return AnomalyLLMReviewer(client=client)

    def _record_threshold_snapshots(self, model_dir) -> None:
        """Record current effective thresholds per entity and metric key.

        Dynamic thresholds: one snapshot per (entity, metric_key) derived from the
        corresponding .joblib artifact.  Static thresholds: one global snapshot
        (entity=None) per metric_key since the value is not entity-specific.
        """
        now = timezone.now()
        snapshots_created = 0

        for dt in DeltaThreshold.objects.all():
            if dt.use_dynamic_threshold:
                # Each artifact file is named "{entity_id}_{metric_key}.joblib".
                # Glob all matching files and create one snapshot per entity.
                artifacts = list(model_dir.glob(f"*_{dt.metric_key}.joblib"))
                any_dynamic = False
                for artifact_path in artifacts:
                    # Extract entity_id — metric_key may contain underscores so use
                    # removesuffix instead of splitting on the first underscore.
                    entity_id_str = artifact_path.stem.removesuffix(f"_{dt.metric_key}")
                    try:
                        entity_id = int(entity_id_str)
                    except ValueError:
                        logger.warning("Unexpected artifact filename, skipping: %s", artifact_path)
                        continue
                    try:
                        artifact = joblib.load(artifact_path)
                        ThresholdSnapshot.objects.create(
                            entity_id=entity_id,
                            metric_key=dt.metric_key,
                            effective_threshold=artifact.p95_abs_change,
                            source=(
                                ThresholdSnapshot.Source.COLD_START
                                if artifact.is_cold_start
                                else ThresholdSnapshot.Source.DYNAMIC
                            ),
                            captured_at=now,
                        )
                        snapshots_created += 1
                        any_dynamic = True
                    except Exception as exc:
                        logger.warning("Could not read artifact %s: %s", artifact_path, exc)
                if any_dynamic:
                    continue  # All per-entity dynamic snapshots recorded; skip static fallback

            # Static threshold (or dynamic with no artifacts): one global row, no entity.
            ThresholdSnapshot.objects.create(
                entity=None,
                metric_key=dt.metric_key,
                effective_threshold=dt.threshold_pct,
                source=ThresholdSnapshot.Source.STATIC,
                captured_at=now,
            )
            snapshots_created += 1

        self.stdout.write(f"  Recorded {snapshots_created} threshold snapshot(s).")

    @staticmethod
    def _compute_span_days(entity_id: int, metric_key: str) -> int:
        """Compute calendar days between first and last reading."""
        dates = (
            Metric.objects.filter(entity_id=entity_id, key=metric_key)
            .exclude(value__isnull=True)
            .order_by("captured_at")
            .values_list("captured_at", flat=True)
        )
        date_list = list(dates[:1]) + list(dates.reverse()[:1])
        if len(date_list) < 2:
            return 0
        return (date_list[1] - date_list[0]).days

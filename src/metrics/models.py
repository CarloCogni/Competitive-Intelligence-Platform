# metrics/models.py
"""
Metrics storage models.

MetricSnapshot     — raw JSON payload per fetch cycle.
Metric             — flat key/value rows extracted from a snapshot.
Alert              — fired when a metric crosses a configured threshold.
DeltaThreshold     — admin-configurable sensitivity per metric key.
ThresholdSnapshot  — point-in-time record of an effective threshold value.
NewsItem           — deduplicated news articles tagged to tracked entities.
MomentumScore      — pre-computed composite momentum for one entity at a point in time.
MomentumConfig     — singleton: per-metric weights and z-score cap for momentum.
"""

import logging

from django.conf import settings  # used by AlertSeen.user FK
from django.db import models
from django.utils import timezone

from core.models import Entity

logger = logging.getLogger(__name__)

# Forward-declare AlertSeen after Alert is defined (see bottom of file)


class MetricSnapshot(models.Model):
    """Raw point-in-time JSON payload fetched for one entity from one source."""

    entity = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name="snapshots")
    # Source identifier: 'github', 'news', 'appstore'
    source = models.CharField(max_length=50)
    data = models.JSONField(help_text="Raw fetched payload.")
    # default= (not auto_now_add) so tests can supply backdated timestamps
    captured_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "Metric Snapshot"
        verbose_name_plural = "Metric Snapshots"
        ordering = ["-captured_at"]
        indexes = [
            models.Index(fields=["entity", "source", "captured_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.entity} / {self.source} @ {self.captured_at:%Y-%m-%d %H:%M}"


class Metric(models.Model):
    """A single named metric value extracted from a MetricSnapshot."""

    entity = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name="metrics")
    source = models.CharField(max_length=50)
    # Metric key, prefixed by source: e.g. 'github_stars', 'appstore_rating'
    key = models.CharField(max_length=100)
    value = models.FloatField(null=True, blank=True)
    # default= (not auto_now_add) so tests can supply backdated timestamps
    captured_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "Metric"
        verbose_name_plural = "Metrics"
        ordering = ["-captured_at"]
        indexes = [
            models.Index(fields=["entity", "key", "captured_at"]),
            models.Index(fields=["key", "captured_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.entity} / {self.key} = {self.value} @ {self.captured_at:%Y-%m-%d %H:%M}"


class Alert(models.Model):
    """Generated when a metric crosses a delta threshold or a notable event is detected."""

    class AlertType(models.TextChoices):
        METRIC_SPIKE = "metric_spike", "Metric Spike"
        NEW_RELEASE = "new_release", "New Release"
        NEW_REPO = "new_repo", "New Repository"
        NEWS_SURGE = "news_surge", "News Surge"
        COMPETITOR_NEW = "competitor_new", "New Competitor Candidate"
        COMPOUND_SPIKE = "compound_spike", "Compound Spike"

    entity = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name="alerts")
    alert_type = models.CharField(max_length=30, choices=AlertType.choices)
    message = models.TextField()
    # Stores context: old_value, new_value, delta_pct, metric_key, etc.
    metadata = models.JSONField(default=dict)
    seen = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Alert"
        verbose_name_plural = "Alerts"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["entity", "created_at"]),
            models.Index(fields=["seen", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"[{self.get_alert_type_display()}] {self.entity} — {self.created_at:%Y-%m-%d}"


class DeltaThreshold(models.Model):
    """
    Admin-configurable alert sensitivity per metric key.

    Replaces the hardcoded THRESHOLDS dict in delta.py so thresholds
    can be tuned at runtime without a code change or redeploy.
    """

    metric_key = models.CharField(max_length=100, unique=True)
    threshold_pct = models.FloatField(
        help_text="Fractional threshold, e.g. 0.20 = 20 %. Alert fires when |Δ| ≥ this."
    )
    lookback_days = models.IntegerField(
        default=7,
        help_text="Number of days to look back for the baseline reading.",
    )
    use_dynamic_threshold = models.BooleanField(
        default=False,
        help_text=(
            "When True, Isolation Forest replaces the static threshold_pct check. "
            "Requires a trained model (run train_anomaly_models). "
            "Falls back to static threshold if model is absent."
        ),
    )

    class Meta:
        verbose_name = "Delta Threshold"
        verbose_name_plural = "Delta Thresholds"
        ordering = ["metric_key"]

    def __str__(self) -> str:
        mode = "dynamic" if self.use_dynamic_threshold else f"{self.threshold_pct * 100:.0f}%"
        return f"{self.metric_key}: {mode} / {self.lookback_days}d"


class ThresholdSnapshot(models.Model):
    """
    Point-in-time record of an effective threshold value.

    Captures both static thresholds (from DeltaThreshold.threshold_pct) and
    dynamic thresholds (from AnomalyArtifact.p95_abs_change) so that threshold
    evolution can be visualized over time in the AI Observatory.
    """

    class Source(models.TextChoices):
        STATIC = "static", "Static"
        DYNAMIC = "dynamic", "Dynamic"
        COLD_START = "cold_start", "Cold Start"

    entity = models.ForeignKey(
        "core.Entity",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="threshold_snapshots",
        help_text="Entity this threshold applies to. Null for static/global snapshots.",
    )
    metric_key = models.CharField(max_length=100)
    effective_threshold = models.FloatField(
        help_text="The effective fractional threshold at this point in time (e.g. 0.20 = 20%).",
    )
    source = models.CharField(
        max_length=10,
        choices=Source.choices,
        help_text="Whether this threshold came from static config, trained model, or cold start.",
    )
    captured_at = models.DateTimeField(
        default=timezone.now,
        help_text="When this snapshot was recorded.",
    )

    class Meta:
        verbose_name = "Threshold Snapshot"
        verbose_name_plural = "Threshold Snapshots"
        ordering = ["-captured_at"]
        indexes = [
            models.Index(fields=["metric_key", "captured_at"]),
            models.Index(fields=["entity", "metric_key", "captured_at"]),
        ]

    def __str__(self) -> str:
        entity_label = self.entity.name if self.entity_id else "global"
        return (
            f"{entity_label} / {self.metric_key}: {self.effective_threshold * 100:.1f}% "
            f"({self.source}) @ {self.captured_at:%Y-%m-%d}"
        )


class NewsItem(models.Model):
    """A deduplicated news article tagged to one or more tracked entities."""

    title = models.CharField(max_length=500)
    # URL is the deduplication key — unique constraint prevents double-ingestion
    url = models.URLField(max_length=1000, unique=True)
    source_name = models.CharField(max_length=200, blank=True)
    entities = models.ManyToManyField(Entity, related_name="news_items", blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    fetched_at = models.DateTimeField(auto_now_add=True)

    # Scraped article body (cached for LLM competitor detection)
    content = models.TextField(
        blank=True,
        null=True,
        help_text="Scraped article body text. Null = not yet scraped.",
    )
    content_scraped_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When scrape was attempted. Non-null + empty content = scrape failed.",
    )

    # LLM sentiment enrichment fields
    sentiment = models.CharField(
        max_length=20,
        blank=True,
        default="",
        help_text="LLM-classified sentiment: positive, neutral, negative, mixed.",
    )
    signal_type = models.CharField(
        max_length=30,
        blank=True,
        default="",
        help_text="LLM-classified signal: product_launch, partnership, funding, "
        "vulnerability, regulation, hiring, opinion, other.",
    )
    llm_summary = models.TextField(
        blank=True,
        default="",
        help_text="1-2 sentence LLM-generated strategic summary.",
    )
    key_quote = models.TextField(
        blank=True,
        default="",
        help_text="Most strategically relevant sentence extracted by LLM (verbatim).",
    )
    enriched_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When LLM enrichment was performed. Null = not yet enriched.",
    )

    class Meta:
        verbose_name = "News Item"
        verbose_name_plural = "News Items"
        ordering = ["-published_at"]
        indexes = [
            models.Index(fields=["published_at"]),
            models.Index(fields=["sentiment"]),
            models.Index(fields=["signal_type"]),
            models.Index(fields=["enriched_at"]),
        ]

    @property
    def signal_type_label(self) -> str:
        """Human-readable label for the signal_type field."""
        return self.signal_type.replace("_", " ").title() if self.signal_type else ""

    def __str__(self) -> str:
        return self.title[:80]


class CandidateEntity(models.Model):
    """
    A potential new competitor extracted by the LLM from news articles.

    The LLM analyzes article titles and scraped content to identify companies,
    products, and protocols not already tracked. Each candidate includes a
    category, relevance score, and reason from the LLM. Once mention_count
    crosses a configurable threshold, a COMPETITOR_NEW alert fires.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROMOTED = "promoted", "Promoted"
        DISMISSED = "dismissed", "Dismissed"

    name = models.CharField(max_length=200, unique=True)
    mention_count = models.PositiveIntegerField(default=1)
    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)
    sample_urls = models.JSONField(
        default=list,
        help_text="List of sample article URLs where this name was mentioned.",
    )
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
    )
    promoted_entity = models.ForeignKey(
        Entity,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="candidate_source",
    )
    alert_fired = models.BooleanField(
        default=False,
        help_text="True once a COMPETITOR_NEW alert has been created for this candidate.",
    )

    # LLM-enriched fields
    category = models.CharField(
        max_length=20,
        blank=True,
        help_text="LLM-assigned category: competitor, exchange, wallet, protocol, infrastructure, media, other.",
    )
    relevance_score = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="LLM-assigned relevance score (1-10) for competitive relevance.",
    )
    llm_reason = models.TextField(
        blank=True,
        help_text="LLM-provided explanation for why this entity was extracted.",
    )

    class Meta:
        verbose_name = "Candidate Entity"
        verbose_name_plural = "Candidate Entities"
        ordering = ["-mention_count"]
        indexes = [
            models.Index(fields=["status", "mention_count"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.mention_count} mentions, {self.status})"


class AlertSeen(models.Model):
    """
    Per-user alert dismissal record with explicit feedback signal.

    Replaces the global Alert.seen flag for dashboard users so that one
    person triaging alerts does not hide them from everyone else.
    A row here means the given user has dismissed the given alert.

    The ``useful`` field carries the GA training signal:
        True  — user marked the alert as relevant (positive label)
        False — user marked the alert as noise (negative label)
        None  — bulk dismiss or legacy row (excluded from GA training)
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="seen_alerts",
    )
    alert = models.ForeignKey(
        Alert,
        on_delete=models.CASCADE,
        related_name="seen_by",
    )
    seen_at = models.DateTimeField(auto_now_add=True)
    useful = models.BooleanField(
        null=True,
        default=None,
        help_text="GA training signal: True=relevant, False=noise, None=neutral/excluded",
    )

    class Meta:
        verbose_name = "Alert Seen"
        verbose_name_plural = "Alerts Seen"
        unique_together = [("user", "alert")]
        indexes = [
            models.Index(fields=["user", "alert"]),
        ]

    def __str__(self) -> str:
        return f"{self.user} saw [{self.alert_id}] @ {self.seen_at:%Y-%m-%d}"


class GARun(models.Model):
    """
    Metadata for a single GA compound-rule optimization run.

    Tracks hyperparameters, labeled-data counts, and outcome so that runs
    are auditable and reproducible. CompoundRule rows link back here via FK.
    """

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        NO_RULES = "no_rules", "No Rules"
        FAILED = "failed", "Failed"

    status = models.CharField(max_length=12, choices=Status.choices, default=Status.RUNNING)
    population_size = models.IntegerField()
    generations = models.IntegerField()
    mutation_rate = models.FloatField()
    crossover_rate = models.FloatField()
    labeled_positives = models.IntegerField(
        default=0,
        help_text="Number of dismissed-alert events used as positive labels.",
    )
    labeled_negatives = models.IntegerField(
        default=0,
        help_text="Number of ignored/absent-alert events used as negative labels.",
    )
    best_fitness = models.FloatField(null=True, blank=True)
    rules_produced = models.IntegerField(default=0)
    config = models.JSONField(
        default=dict,
        help_text="Full hyperparameter snapshot for reproducibility.",
    )
    fitness_history = models.JSONField(
        default=list,
        help_text="Best fitness score per generation, e.g. [0.12, 0.15, 0.21, ...].",
    )
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "GA Run"
        verbose_name_plural = "GA Runs"
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return f"GARun #{self.pk} [{self.status}] — {self.started_at:%Y-%m-%d %H:%M}"


class MomentumScore(models.Model):
    """
    Pre-computed composite momentum score for one entity at one point in time.

    Aggregates the 7-day velocity of all available metrics into a single
    z-score-based composite that is comparable across entities regardless
    of which metrics each entity has.
    """

    entity = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name="momentum_scores")
    score = models.FloatField(help_text="Composite weighted z-score of 7-day velocities.")
    acceleration = models.FloatField(
        null=True,
        blank=True,
        help_text="Day-over-day change in momentum score (today - previous).",
    )
    metrics_used = models.IntegerField(help_text="Number of metric keys that contributed.")
    detail = models.JSONField(
        default=dict,
        help_text='Per-metric breakdown: {"key": {"velocity": float, "z_score": float, "weight": float}, ...}',
    )
    computed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "Momentum Score"
        verbose_name_plural = "Momentum Scores"
        ordering = ["-computed_at"]
        indexes = [
            models.Index(fields=["entity", "computed_at"]),
            models.Index(fields=["computed_at", "score"]),
        ]

    def __str__(self) -> str:
        return f"{self.entity} momentum={self.score:+.3f} ({self.metrics_used} metrics) @ {self.computed_at:%Y-%m-%d}"


class MomentumConfig(models.Model):
    """
    Singleton configuration for the momentum score computation.

    Stores per-metric weights and the z-score cap. Editable via Django admin.
    """

    weights = models.JSONField(
        default=dict,
        help_text='Metric key → weight, e.g. {"github_stars": 2.0}. Missing keys default to 1.0.',
    )
    z_cap = models.FloatField(
        default=3.0,
        help_text="Maximum absolute z-score before capping (prevents outlier domination).",
    )

    class Meta:
        verbose_name = "Momentum Config"
        verbose_name_plural = "Momentum Config"

    def save(self, *args, **kwargs) -> None:
        """Enforce singleton: always save as pk=1."""
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get(cls) -> "MomentumConfig":
        """Return the singleton config, creating with defaults if absent."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self) -> str:
        n = len(self.weights) if isinstance(self.weights, dict) else 0
        return f"MomentumConfig (z_cap={self.z_cap}, {n} custom weights)"


class CompoundRule(models.Model):
    """
    A GA-discovered compound alert rule: AND-conjunction of metric conditions.

    Each condition is a dict: {"metric_key": str, "operator": "gte"|"lte",
    "threshold_pct": float}. The rule fires when ALL conditions are satisfied
    for a given entity's current metric deltas.
    """

    name = models.CharField(
        max_length=200,
        blank=True,
        help_text="Auto-generated human-readable summary of conditions.",
    )
    conditions = models.JSONField(
        help_text=(
            'List of conditions: [{"metric_key": "github_stars", '
            '"operator": "gte", "threshold_pct": 0.15}, ...]'
        ),
    )
    fitness_score = models.FloatField(default=0.0)
    precision = models.FloatField(default=0.0)
    recall = models.FloatField(default=0.0)
    f1_score = models.FloatField(default=0.0)
    true_positives = models.IntegerField(default=0)
    false_positives = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_by_run = models.ForeignKey(
        GARun,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rules",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Compound Rule"
        verbose_name_plural = "Compound Rules"
        ordering = ["-fitness_score"]
        indexes = [
            models.Index(fields=["is_active", "fitness_score"]),
        ]

    def __str__(self) -> str:
        n = len(self.conditions) if isinstance(self.conditions, list) else 0
        return f"Rule #{self.pk} ({n} conditions, fitness={self.fitness_score:.3f})"

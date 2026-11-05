# metrics/admin.py
"""Django admin configuration for metrics models."""

from django.contrib import admin

from .models import (
    Alert,
    CandidateEntity,
    CompoundRule,
    DeltaThreshold,
    GARun,
    Metric,
    MetricSnapshot,
    MomentumConfig,
    MomentumScore,
    NewsItem,
    ThresholdSnapshot,
)


@admin.register(MetricSnapshot)
class MetricSnapshotAdmin(admin.ModelAdmin):
    """Admin for MetricSnapshot."""

    list_display = ("entity", "source", "captured_at")
    list_filter = ("source", "entity")
    readonly_fields = ("captured_at",)
    date_hierarchy = "captured_at"


@admin.register(Metric)
class MetricAdmin(admin.ModelAdmin):
    """Admin for Metric."""

    list_display = ("entity", "source", "key", "value", "captured_at")
    list_filter = ("source", "key")
    search_fields = ("entity__name", "key")
    date_hierarchy = "captured_at"


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    """Admin for Alert."""

    list_display = ("entity", "alert_type", "message", "seen", "created_at")
    list_filter = ("alert_type", "seen", "entity")
    readonly_fields = ("created_at",)
    date_hierarchy = "created_at"
    actions = ["mark_seen"]

    @admin.action(description="Mark selected alerts as seen")
    def mark_seen(self, request, queryset):
        queryset.update(seen=True)


@admin.register(DeltaThreshold)
class DeltaThresholdAdmin(admin.ModelAdmin):
    """Admin for DeltaThreshold — tune alert sensitivity per metric key without code changes."""

    list_display = ("metric_key", "threshold_display", "lookback_days", "use_dynamic_threshold")
    list_editable = ("use_dynamic_threshold",)
    ordering = ("metric_key",)

    @admin.display(description="Static Threshold")
    def threshold_display(self, obj: DeltaThreshold) -> str:
        """Show threshold as a human-readable percentage."""
        return f"{obj.threshold_pct * 100:.1f}%"


@admin.register(NewsItem)
class NewsItemAdmin(admin.ModelAdmin):
    """Admin for NewsItem."""

    list_display = ("title", "source_name", "published_at", "fetched_at")
    list_filter = ("source_name",)
    search_fields = ("title", "url")
    filter_horizontal = ("entities",)
    date_hierarchy = "published_at"


@admin.register(GARun)
class GARunAdmin(admin.ModelAdmin):
    """Admin for GARun — view GA optimization run history."""

    list_display = (
        "pk",
        "status",
        "population_size",
        "generations",
        "best_fitness",
        "rules_produced",
        "started_at",
    )
    list_filter = ("status",)
    readonly_fields = ("started_at", "finished_at")
    ordering = ("-started_at",)


@admin.register(CompoundRule)
class CompoundRuleAdmin(admin.ModelAdmin):
    """Admin for CompoundRule — toggle GA-discovered alert rules on/off."""

    list_display = (
        "name",
        "fitness_score",
        "precision",
        "recall",
        "is_active",
        "created_at",
    )
    list_filter = ("is_active", "created_by_run")
    list_editable = ("is_active",)
    readonly_fields = ("created_at",)
    ordering = ("-fitness_score",)


@admin.register(ThresholdSnapshot)
class ThresholdSnapshotAdmin(admin.ModelAdmin):
    """Admin for ThresholdSnapshot — view threshold evolution history."""

    list_display = ("metric_key", "effective_threshold_display", "source", "captured_at")
    list_filter = ("source", "metric_key")
    date_hierarchy = "captured_at"

    @admin.display(description="Threshold")
    def effective_threshold_display(self, obj: ThresholdSnapshot) -> str:
        """Show threshold as a human-readable percentage."""
        return f"{obj.effective_threshold * 100:.1f}%"


@admin.register(CandidateEntity)
class CandidateEntityAdmin(admin.ModelAdmin):
    """Admin for CandidateEntity — review and promote competitor candidates."""

    list_display = ("name", "mention_count", "status", "alert_fired", "first_seen", "last_seen")
    list_filter = ("status", "alert_fired")
    search_fields = ("name",)
    readonly_fields = ("first_seen", "last_seen")
    ordering = ("-mention_count",)


@admin.register(MomentumScore)
class MomentumScoreAdmin(admin.ModelAdmin):
    """Admin for MomentumScore — view entity momentum score history."""

    list_display = ("entity", "score", "acceleration", "metrics_used", "computed_at")
    list_filter = ("entity",)
    search_fields = ("entity__name",)
    date_hierarchy = "computed_at"
    readonly_fields = ("computed_at",)
    ordering = ("-computed_at", "-score")


@admin.register(MomentumConfig)
class MomentumConfigAdmin(admin.ModelAdmin):
    """Admin for MomentumConfig — tune momentum score weights and z-cap."""

    list_display = ("__str__", "z_cap")

    def has_add_permission(self, request) -> bool:
        """Prevent creating multiple config rows — singleton enforced by model."""
        return not MomentumConfig.objects.exists()

    def has_delete_permission(self, request, obj=None) -> bool:
        """Prevent deletion of the singleton config."""
        return False

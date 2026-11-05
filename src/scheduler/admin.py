# scheduler/admin.py
"""Django admin configuration for the scheduler application."""

from django.contrib import admin
from django_apscheduler.admin import DjangoJobAdmin, DjangoJobExecutionAdmin
from django_apscheduler.models import DjangoJob, DjangoJobExecution

from scheduler.models import BatchRun, FetcherConfig


@admin.register(FetcherConfig)
class FetcherConfigAdmin(admin.ModelAdmin):
    """Admin for FetcherConfig — inline editing of intervals and enabled flags."""

    list_display = ["source", "interval_hours", "enabled"]
    list_editable = ["interval_hours", "enabled"]
    ordering = ["source"]


# Unregister django_apscheduler's default registrations so we can override them.
admin.site.unregister(DjangoJob)
admin.site.unregister(DjangoJobExecution)


@admin.register(DjangoJob)
class CustomDjangoJobAdmin(DjangoJobAdmin):
    """Django Jobs — shows all scheduled jobs and their next run time."""

    list_display = ["id", "next_run_time"]
    ordering = ["next_run_time"]


@admin.register(DjangoJobExecution)
class CustomDjangoJobExecutionAdmin(DjangoJobExecutionAdmin):
    """Django Job Executions — full history of every job run, never pruned."""

    list_display = ["id", "job", "status", "run_time", "duration"]
    list_filter = ["job", "status"]
    date_hierarchy = "run_time"
    ordering = ["-run_time"]


@admin.register(BatchRun)
class BatchRunAdmin(admin.ModelAdmin):
    """Batch Runs — per-execution record with entity-level outcome detail."""

    list_display = [
        "id",
        "job_id",
        "triggered_at",
        "status",
        "entities_total",
        "entities_success",
        "entities_failed",
        "duration_display",
    ]
    list_filter = ["job_id", "status"]
    readonly_fields = [
        "job_id",
        "triggered_at",
        "started_at",
        "finished_at",
        "status",
        "trigger_reason",
        "entities_total",
        "entities_success",
        "entities_failed",
        "error_log",
    ]
    ordering = ["-triggered_at"]
    date_hierarchy = "triggered_at"

    @admin.display(description="Duration")
    def duration_display(self, obj: BatchRun) -> str:
        """Return a human-readable duration string."""
        secs = obj.duration_seconds
        if secs is None:
            return "—"
        return f"{secs:.1f}s"

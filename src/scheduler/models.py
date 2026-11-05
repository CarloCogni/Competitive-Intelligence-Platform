# scheduler/models.py
"""
Models for the scheduler application.

FetcherConfig stores per-source fetch intervals and enabled flags,
allowing the admin to adjust scheduling without touching code.

BatchRun records every job execution with per-entity outcome detail,
providing visibility into scheduler health and failure patterns.
"""

import logging

from django.db import models

logger = logging.getLogger(__name__)


class FetcherConfig(models.Model):
    """Per-source scheduler configuration editable via the Django admin."""

    SOURCE_CHOICES = [
        ("github", "GitHub"),
        ("news", "News"),
        ("appstore", "App Store"),
        ("twitter", "Twitter / X"),
        ("reddit", "Reddit"),
        ("playstore", "Play Store"),
        ("changelog", "Changelog"),
    ]

    source = models.CharField(
        max_length=20,
        choices=SOURCE_CHOICES,
        unique=True,
        help_text="Data source this config applies to.",
    )
    interval_hours = models.PositiveIntegerField(
        default=24,
        help_text="How often to fetch data for this source, in hours.",
    )
    enabled = models.BooleanField(
        default=True,
        help_text="Uncheck to disable this fetcher entirely.",
    )

    class Meta:
        verbose_name = "Fetcher Config"
        verbose_name_plural = "Fetcher Configs"
        ordering = ["source"]

    def __str__(self) -> str:
        """Return a human-readable representation of the config."""
        status = "enabled" if self.enabled else "disabled"
        return f"{self.get_source_display()} — every {self.interval_hours}h ({status})"


class BatchRun(models.Model):
    """
    Records one execution of a scheduled fetcher job.

    Created at job start (status=RUNNING), updated at completion with per-entity
    success/failure counts and an error_log dict keyed by entity slug.
    """

    class Status(models.TextChoices):
        """Possible terminal states for a batch run."""

        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        PARTIAL = "partial", "Partial (some entities failed)"
        FAILED = "failed", "Failed"

    class TriggerReason(models.TextChoices):
        """How the job was triggered."""

        SCHEDULED = "scheduled", "Scheduled"
        MANUAL = "manual", "Manual"

    # Which APScheduler job produced this run (e.g. 'github_fetcher')
    job_id = models.CharField(
        max_length=50,
        db_index=True,
        help_text="APScheduler job ID (e.g. 'github_fetcher').",
    )
    triggered_at = models.DateTimeField(
        help_text="When the job was triggered.",
    )
    started_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the job function body started executing.",
    )
    finished_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the job function body completed.",
    )
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.RUNNING,
    )
    trigger_reason = models.CharField(
        max_length=10,
        choices=TriggerReason.choices,
        default=TriggerReason.SCHEDULED,
    )
    entities_total = models.PositiveIntegerField(default=0)
    entities_success = models.PositiveIntegerField(default=0)
    entities_failed = models.PositiveIntegerField(default=0)
    # Per-entity error messages: {"entity-slug": "error message", ...}
    error_log = models.JSONField(
        default=dict,
        blank=True,
        help_text="Per-entity error messages keyed by entity slug.",
    )

    class Meta:
        verbose_name = "Batch Run"
        verbose_name_plural = "Batch Runs"
        ordering = ["-triggered_at"]
        indexes = [
            models.Index(fields=["job_id", "triggered_at"]),
            models.Index(fields=["status", "triggered_at"]),
        ]

    def __str__(self) -> str:
        """Return a concise summary of this batch run."""
        ts = self.triggered_at.strftime("%Y-%m-%d %H:%M")
        return f"{self.job_id} @ {ts} — {self.get_status_display()}"

    @property
    def duration_seconds(self) -> float | None:
        """Return wall-clock duration in seconds, or None if not finished."""
        if self.started_at and self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None

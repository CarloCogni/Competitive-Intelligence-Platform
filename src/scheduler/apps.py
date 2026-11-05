# scheduler/apps.py
"""App configuration for the scheduler application."""

import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class SchedulerConfig(AppConfig):
    """Scheduler app: APScheduler wiring for periodic fetcher jobs."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "scheduler"
    verbose_name = "Scheduler"

    def ready(self) -> None:
        """Start APScheduler and connect signals when Django boots."""
        self._connect_signals()

        from django.conf import settings

        if not settings.RUN_SCHEDULER:
            logger.info("Scheduler disabled (RUN_SCHEDULER=false).")
            return

        # Guard against double-start in Django's auto-reloader
        import os

        if os.environ.get("RUN_MAIN") != "true":
            return

        from scheduler.jobs import start_scheduler

        start_scheduler()

    def _connect_signals(self) -> None:
        """Wire post_save on FetcherConfig to live-reschedule the affected job."""
        from django.db.models.signals import post_save

        from scheduler.models import FetcherConfig

        def on_config_save(sender, instance: FetcherConfig, **kwargs) -> None:  # type: ignore[type-arg]
            from scheduler.jobs import reschedule_job

            reschedule_job(instance.source)

        post_save.connect(on_config_save, sender=FetcherConfig, weak=False)

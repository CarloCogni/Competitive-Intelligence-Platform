# core/apps.py
"""App configuration for the core application."""

from django.apps import AppConfig


class CoreConfig(AppConfig):
    """Core app: Entity, Vertical, UserProfile models, seed command."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = "Core"

    def ready(self) -> None:
        """Register signal handlers."""
        import core.signals  # noqa: F401

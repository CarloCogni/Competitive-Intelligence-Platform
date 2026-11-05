# metrics/apps.py
"""App configuration for the metrics application."""

from django.apps import AppConfig


class MetricsConfig(AppConfig):
    """Metrics app: storage models and delta detection."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "metrics"
    verbose_name = "Metrics"

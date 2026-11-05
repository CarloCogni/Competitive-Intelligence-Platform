# fetchers/apps.py
"""App configuration for the fetchers application."""

from django.apps import AppConfig


class FetchersConfig(AppConfig):
    """Fetchers app: data ingestion from external APIs."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "fetchers"
    verbose_name = "Fetchers"

# dashboard/apps.py
"""App configuration for the dashboard application."""

from django.apps import AppConfig


class DashboardConfig(AppConfig):
    """Dashboard app: views and templates for the competitive intelligence UI."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "dashboard"
    verbose_name = "Dashboard"

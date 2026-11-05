# briefings/apps.py
"""Django app configuration for the briefings application."""

from django.apps import AppConfig


class BriefingsConfig(AppConfig):
    """App config for the AI-powered weekly briefing generator."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "briefings"
    verbose_name = "Briefings"

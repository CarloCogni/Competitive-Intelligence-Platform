# scheduler/migrations/0002_seed_fetcherconfig_defaults.py
"""Data migration: seed default FetcherConfig rows for all three sources."""

from django.db import migrations

DEFAULTS = [
    {"source": "github", "interval_hours": 24, "enabled": True},
    {"source": "news", "interval_hours": 2, "enabled": True},
    {"source": "appstore", "interval_hours": 12, "enabled": True},
]


def seed_defaults(apps, schema_editor):
    """Create default FetcherConfig rows if they do not already exist."""
    FetcherConfig = apps.get_model("scheduler", "FetcherConfig")
    for defaults in DEFAULTS:
        FetcherConfig.objects.get_or_create(source=defaults["source"], defaults=defaults)


def remove_defaults(apps, schema_editor):
    """Reverse: delete the seeded rows."""
    FetcherConfig = apps.get_model("scheduler", "FetcherConfig")
    FetcherConfig.objects.filter(source__in=[d["source"] for d in DEFAULTS]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("scheduler", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_defaults, reverse_code=remove_defaults),
    ]

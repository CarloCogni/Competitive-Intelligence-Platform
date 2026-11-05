# scheduler/migrations/0004_seed_social_fetcherconfig.py
"""Data migration: seed default FetcherConfig rows for Twitter and Reddit fetchers."""

from django.db import migrations

DEFAULTS = [
    {"source": "twitter", "interval_hours": 1, "enabled": True},
    {"source": "reddit", "interval_hours": 6, "enabled": True},
]


def seed_defaults(apps, schema_editor):
    """Create FetcherConfig rows for social sources if they do not already exist."""
    FetcherConfig = apps.get_model("scheduler", "FetcherConfig")
    for defaults in DEFAULTS:
        FetcherConfig.objects.get_or_create(source=defaults["source"], defaults=defaults)


def remove_defaults(apps, schema_editor):
    """Reverse: delete the seeded rows."""
    FetcherConfig = apps.get_model("scheduler", "FetcherConfig")
    FetcherConfig.objects.filter(source__in=[d["source"] for d in DEFAULTS]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("scheduler", "0003_add_batchrun"),
    ]

    operations = [
        migrations.RunPython(seed_defaults, reverse_code=remove_defaults),
    ]

# metrics/migrations/0007_seed_github_contributors_deltathreshold.py
"""Data migration: seed DeltaThreshold row for the github_contributors metric."""

from django.db import migrations


def seed_threshold(apps, schema_editor):
    """Add DeltaThreshold for github_contributors (idempotent via get_or_create)."""
    DeltaThreshold = apps.get_model("metrics", "DeltaThreshold")
    DeltaThreshold.objects.get_or_create(
        metric_key="github_contributors",
        defaults={"threshold_pct": 0.10, "lookback_days": 30},
    )


def unseed_threshold(apps, schema_editor):
    """Remove the seeded row (reverse migration)."""
    DeltaThreshold = apps.get_model("metrics", "DeltaThreshold")
    DeltaThreshold.objects.filter(metric_key="github_contributors").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("metrics", "0006_seed_social_deltathreshold_data"),
    ]

    operations = [
        migrations.RunPython(seed_threshold, reverse_code=unseed_threshold),
    ]

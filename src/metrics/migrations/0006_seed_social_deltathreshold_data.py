# metrics/migrations/0006_seed_social_deltathreshold_data.py
"""Data migration: seed DeltaThreshold rows for social fetcher metrics."""

from django.db import migrations

# Format: metric_key -> (threshold_pct fractional, lookback_days)
SOCIAL_THRESHOLDS: dict[str, tuple[float, int]] = {
    "twitter_mention_count": (0.50, 7),
    "twitter_total_engagement": (0.50, 7),
    "twitter_avg_engagement": (0.30, 7),
    "reddit_mention_count": (0.50, 7),
}


def seed_thresholds(apps, schema_editor):
    """Create one DeltaThreshold row per social metric key (idempotent via get_or_create)."""
    DeltaThreshold = apps.get_model("metrics", "DeltaThreshold")
    for key, (threshold_pct, lookback_days) in SOCIAL_THRESHOLDS.items():
        DeltaThreshold.objects.get_or_create(
            metric_key=key,
            defaults={"threshold_pct": threshold_pct, "lookback_days": lookback_days},
        )


def unseed_thresholds(apps, schema_editor):
    """Remove the seeded rows (reverse migration)."""
    DeltaThreshold = apps.get_model("metrics", "DeltaThreshold")
    DeltaThreshold.objects.filter(metric_key__in=SOCIAL_THRESHOLDS).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("metrics", "0005_alertseen"),
    ]

    operations = [
        migrations.RunPython(seed_thresholds, reverse_code=unseed_thresholds),
    ]

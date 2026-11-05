# metrics/migrations/0004_seed_deltathreshold_data.py
"""Data migration: seed initial DeltaThreshold rows from the former hardcoded THRESHOLDS dict."""

from django.db import migrations

# Initial values matching the former hardcoded dict in metrics/delta.py.
# Format: metric_key -> (threshold_pct fractional, lookback_days)
INITIAL_THRESHOLDS: dict[str, tuple[float, int]] = {
    "github_stars": (0.20, 7),
    "github_forks": (0.20, 7),
    "github_open_issues": (0.30, 7),
    "appstore_rating": (0.10, 7),
    "appstore_rating_count": (0.15, 7),
    "news_article_count": (2.00, 7),  # 200 % — detects news surges
}


def seed_thresholds(apps, schema_editor):
    """Create one DeltaThreshold row per initial metric key (idempotent via get_or_create)."""
    DeltaThreshold = apps.get_model("metrics", "DeltaThreshold")
    for key, (threshold_pct, lookback_days) in INITIAL_THRESHOLDS.items():
        DeltaThreshold.objects.get_or_create(
            metric_key=key,
            defaults={"threshold_pct": threshold_pct, "lookback_days": lookback_days},
        )


def unseed_thresholds(apps, schema_editor):
    """Remove the seeded rows (reverse migration)."""
    DeltaThreshold = apps.get_model("metrics", "DeltaThreshold")
    DeltaThreshold.objects.filter(metric_key__in=INITIAL_THRESHOLDS).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("metrics", "0003_add_deltathreshold_model"),
    ]

    operations = [
        migrations.RunPython(seed_thresholds, reverse_code=unseed_thresholds),
    ]

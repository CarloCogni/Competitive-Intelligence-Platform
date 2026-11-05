# metrics/tests/conftest.py
"""Shared fixtures for the metrics test suite."""

import pytest

from metrics.models import DeltaThreshold


@pytest.fixture(autouse=True)
def seed_thresholds(db):
    """
    Seed DeltaThreshold rows before every test in this package.

    Mirrors the values from migration 0004. Required because run_delta_detection()
    now reads thresholds from the DB instead of the former hardcoded dict.
    """
    # ignore_conflicts=True: the data migration already seeds these rows when the
    # test DB is created from scratch. This fixture is a safety net for test runs
    # that do not apply migrations (e.g. --no-migrations / TransactionTestCase resets).
    DeltaThreshold.objects.bulk_create(
        [
            DeltaThreshold(metric_key="github_stars", threshold_pct=0.20, lookback_days=7),
            DeltaThreshold(metric_key="github_forks", threshold_pct=0.20, lookback_days=7),
            DeltaThreshold(metric_key="github_open_issues", threshold_pct=0.30, lookback_days=7),
            DeltaThreshold(metric_key="appstore_rating", threshold_pct=0.10, lookback_days=7),
            DeltaThreshold(metric_key="appstore_rating_count", threshold_pct=0.15, lookback_days=7),
            DeltaThreshold(metric_key="news_article_count", threshold_pct=2.00, lookback_days=7),
            DeltaThreshold(metric_key="github_contributors", threshold_pct=0.10, lookback_days=30),
        ],
        ignore_conflicts=True,
    )


@pytest.fixture
def dynamic_threshold(db):
    """DeltaThreshold with use_dynamic_threshold=True for github_stars."""
    DeltaThreshold.objects.filter(metric_key="github_stars").update(use_dynamic_threshold=True)
    return DeltaThreshold.objects.get(metric_key="github_stars")

# fetchers/tests/test_changelog.py
"""Tests for ChangelogFetcher. feedparser calls are mocked via unittest.mock."""

from unittest.mock import MagicMock, patch

import pytest
from freezegun import freeze_time

from core.tests.factories import EntityFactory
from fetchers.changelog import ChangelogFetcher
from metrics.models import Metric, MetricSnapshot


@pytest.fixture
def entity(db):
    return EntityFactory(github_org="test-org", github_repo="test-repo")


@pytest.fixture
def entity_no_config(db):
    return EntityFactory(github_org="", github_repo="")


def _mock_feed(entries=None, bozo=False, bozo_exception=None):
    """Return a mock feedparser result."""
    feed = MagicMock()
    feed.bozo = bozo
    feed.bozo_exception = bozo_exception
    feed.entries = entries or []
    return feed


def _make_entry(
    title="v1.0.0",
    tag="v1.0.0",
    updated="2024-06-01T12:00:00Z",
    link="https://github.com/test-org/test-repo/releases/tag/v1.0.0",
):
    """Create a mock Atom feed entry."""
    entry = MagicMock()
    entry.get = lambda key, default="": {
        "title": title,
        "id": f"https://github.com/test-org/test-repo/releases/tag/{tag}",
        "updated": updated,
        "link": link,
    }.get(key, default)
    return entry


@pytest.mark.django_db
class TestChangelogFetcherFetch:
    """Tests for ChangelogFetcher.fetch() — Atom feed parsing."""

    @patch("fetchers.changelog.feedparser.parse")
    def test_happy_path(self, mock_parse, entity):
        entries = [_make_entry(title="v1.0.0"), _make_entry(title="v0.9.0", tag="v0.9.0")]
        mock_parse.return_value = _mock_feed(entries=entries)

        data = ChangelogFetcher(entity).fetch()

        assert data["releases_total"] == 2
        assert data["latest_tag"] == "v1.0.0"
        assert data["latest_title"] == "v1.0.0"
        assert "entries" in data

    def test_no_github_org_returns_empty(self, entity_no_config):
        data = ChangelogFetcher(entity_no_config).fetch()
        assert data == {}

    @patch("fetchers.changelog.feedparser.parse")
    def test_bozo_no_entries_returns_empty(self, mock_parse, entity):
        mock_parse.return_value = _mock_feed(bozo=True, bozo_exception=Exception("parse error"))
        data = ChangelogFetcher(entity).fetch()
        assert data == {}

    @patch("fetchers.changelog.feedparser.parse")
    def test_empty_feed_returns_empty(self, mock_parse, entity):
        mock_parse.return_value = _mock_feed(entries=[])
        data = ChangelogFetcher(entity).fetch()
        assert data == {}

    @patch("fetchers.changelog.feedparser.parse")
    def test_bozo_with_entries_still_processes(self, mock_parse, entity):
        """If feed has bozo=True but entries exist, it should still return data."""
        entries = [_make_entry()]
        mock_parse.return_value = _mock_feed(entries=entries, bozo=True)
        data = ChangelogFetcher(entity).fetch()
        assert data["releases_total"] == 1


@pytest.mark.django_db
class TestChangelogFetcherExtractMetrics:
    """Tests for ChangelogFetcher.extract_metrics() — raw data to flat metrics."""

    @freeze_time("2024-07-01T12:00:00Z")
    def test_extracts_all_keys(self, entity):
        data = {
            "releases_total": 5,
            "latest_published": "2024-06-01T12:00:00Z",
        }
        metrics = ChangelogFetcher(entity).extract_metrics(data)
        assert metrics["changelog_releases_total"] == 5.0
        assert metrics["changelog_latest_release_days_ago"] == 30.0

    def test_missing_fields_produce_empty(self, entity):
        metrics = ChangelogFetcher(entity).extract_metrics({})
        assert metrics == {}

    def test_missing_published_skips_days_ago(self, entity):
        data = {"releases_total": 3}
        metrics = ChangelogFetcher(entity).extract_metrics(data)
        assert "changelog_latest_release_days_ago" not in metrics


@pytest.mark.django_db
class TestChangelogFetcherRun:
    """Tests for ChangelogFetcher.run() — full lifecycle integration."""

    @patch("fetchers.changelog.feedparser.parse")
    def test_run_persists_snapshot_and_metrics(self, mock_parse, entity):
        entries = [_make_entry()]
        mock_parse.return_value = _mock_feed(entries=entries)

        ChangelogFetcher(entity).run()

        assert MetricSnapshot.objects.filter(entity=entity, source="changelog").exists()
        assert Metric.objects.filter(entity=entity, key="changelog_releases_total").exists()

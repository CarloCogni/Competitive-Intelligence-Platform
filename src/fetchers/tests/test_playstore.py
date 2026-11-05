# fetchers/tests/test_playstore.py
"""Tests for PlayStoreFetcher. google-play-scraper calls are mocked."""

from unittest.mock import MagicMock, patch

import pytest
from freezegun import freeze_time

from core.tests.factories import EntityFactory
from fetchers.playstore import PlayStoreFetcher
from metrics.models import Metric, MetricSnapshot

PLAY_STORE_ID = "com.example.app"

PLAY_STORE_RESPONSE = {
    "title": "Example App",
    "score": 4.5,
    "ratings": 12000,
    "version": "2.1.0",
    "lastUpdatedOn": "2024-06-15",
}

# Create a fake NotFoundError exception class used by google_play_scraper
_NotFoundError = type("NotFoundError", (Exception,), {})

# Build mock modules for google_play_scraper (lazy import in fetch())
_mock_gplay_module = MagicMock()
_mock_gplay_module.app = MagicMock(return_value=PLAY_STORE_RESPONSE)
_mock_gplay_exceptions = MagicMock(NotFoundError=_NotFoundError)

_GPLAY_MODULES = {
    "google_play_scraper": _mock_gplay_module,
    "google_play_scraper.exceptions": _mock_gplay_exceptions,
}


@pytest.fixture
def entity(db):
    return EntityFactory(play_store_id=PLAY_STORE_ID)


@pytest.fixture
def entity_no_config(db):
    return EntityFactory(play_store_id="")


@pytest.fixture(autouse=True)
def _patch_gplay():
    """Patch google_play_scraper into sys.modules for all tests."""
    with patch.dict("sys.modules", _GPLAY_MODULES):
        # Reset mock state before each test
        _mock_gplay_module.app.reset_mock()
        _mock_gplay_module.app.return_value = PLAY_STORE_RESPONSE
        _mock_gplay_module.app.side_effect = None
        yield


@pytest.mark.django_db
class TestPlayStoreFetcherFetch:
    """Tests for PlayStoreFetcher.fetch() — Play Store API call."""

    def test_happy_path(self, entity):
        data = PlayStoreFetcher(entity).fetch()
        assert data["rating"] == 4.5
        assert data["rating_count"] == 12000
        assert data["version"] == "2.1.0"

    def test_no_play_store_id_returns_empty(self, entity_no_config):
        data = PlayStoreFetcher(entity_no_config).fetch()
        assert data == {}

    def test_not_found_error_returns_empty(self, entity):
        _mock_gplay_module.app.side_effect = _NotFoundError("not found")
        data = PlayStoreFetcher(entity).fetch()
        assert data == {}

    def test_generic_exception_returns_empty(self, entity):
        _mock_gplay_module.app.side_effect = Exception("network error")
        data = PlayStoreFetcher(entity).fetch()
        assert data == {}


@pytest.mark.django_db
class TestPlayStoreFetcherExtractMetrics:
    """Tests for PlayStoreFetcher.extract_metrics() — raw data to flat metrics."""

    @freeze_time("2024-07-15T12:00:00Z")
    def test_extracts_all_keys(self, entity):
        data = {
            "rating": 4.5,
            "rating_count": 12000,
            "last_updated": "2024-06-15",
        }
        metrics = PlayStoreFetcher(entity).extract_metrics(data)
        assert metrics["playstore_rating"] == pytest.approx(4.5)
        assert metrics["playstore_rating_count"] == 12000.0
        assert metrics["playstore_last_update_days_ago"] == 30.0

    def test_missing_fields_produce_empty(self, entity):
        metrics = PlayStoreFetcher(entity).extract_metrics({})
        assert metrics == {}

    def test_none_rating_not_emitted(self, entity):
        data = {"rating": None, "rating_count": None}
        metrics = PlayStoreFetcher(entity).extract_metrics(data)
        assert "playstore_rating" not in metrics


@pytest.mark.django_db
class TestPlayStoreFetcherRun:
    """Tests for PlayStoreFetcher.run() — full lifecycle integration."""

    def test_run_persists_snapshot_and_metrics(self, entity):
        PlayStoreFetcher(entity).run()

        assert MetricSnapshot.objects.filter(entity=entity, source="playstore").exists()
        assert Metric.objects.filter(entity=entity, key="playstore_rating").exists()
        assert Metric.objects.filter(entity=entity, key="playstore_rating_count").exists()

# fetchers/tests/test_appstore.py
"""Tests for AppStoreFetcher. All HTTP calls are mocked via responses library."""

import pytest
import responses as resp

from core.tests.factories import EntityFactory
from fetchers.appstore import AppStoreFetcher
from metrics.models import Metric

ITUNES_URL = "https://itunes.apple.com/lookup"
APP_STORE_ID = "12345"

ITUNES_RESPONSE = {
    "resultCount": 1,
    "results": [
        {
            "trackName": "Test App",
            "averageUserRating": 4.7,
            "userRatingCount": 3000,
            "version": "2.1.0",
            "currentVersionReleaseDate": "2024-03-01T10:00:00Z",
        }
    ],
}


@pytest.fixture
def entity(db):
    return EntityFactory(app_store_id=APP_STORE_ID)


@pytest.fixture
def entity_no_id(db):
    return EntityFactory(app_store_id="")


@pytest.mark.django_db
class TestAppStoreFetcherFetch:
    @resp.activate
    def test_happy_path_returns_payload(self, entity):
        resp.add(resp.GET, ITUNES_URL, json=ITUNES_RESPONSE)
        data = AppStoreFetcher(entity).fetch()
        assert data["rating"] == 4.7
        assert data["rating_count"] == 3000
        assert data["app_name"] == "Test App"

    def test_no_app_store_id_returns_empty(self, entity_no_id):
        data = AppStoreFetcher(entity_no_id).fetch()
        assert data == {}

    @resp.activate
    def test_api_failure_returns_empty(self, entity):
        resp.add(resp.GET, ITUNES_URL, status=500)
        data = AppStoreFetcher(entity).fetch()
        assert data == {}

    @resp.activate
    def test_empty_results_returns_empty(self, entity):
        resp.add(resp.GET, ITUNES_URL, json={"resultCount": 0, "results": []})
        data = AppStoreFetcher(entity).fetch()
        assert data == {}


@pytest.mark.django_db
class TestAppStoreFetcherExtractMetrics:
    def test_extract_metrics_keys(self, entity):
        raw = {"rating": 4.5, "rating_count": 1000}
        metrics = AppStoreFetcher(entity).extract_metrics(raw)
        assert metrics["appstore_rating"] == 4.5
        assert metrics["appstore_rating_count"] == 1000.0

    def test_missing_rating_produces_no_key(self, entity):
        metrics = AppStoreFetcher(entity).extract_metrics({})
        assert "appstore_rating" not in metrics
        assert "appstore_rating_count" not in metrics

    def test_last_update_days_ago_is_non_negative_float(self, entity):
        raw = {
            "rating": 4.5,
            "rating_count": 1000,
            "current_version_release_date": "2024-01-01T00:00:00Z",
        }
        metrics = AppStoreFetcher(entity).extract_metrics(raw)
        assert "appstore_last_update_days_ago" in metrics
        assert isinstance(metrics["appstore_last_update_days_ago"], float)
        assert metrics["appstore_last_update_days_ago"] >= 0

    def test_missing_release_date_produces_no_last_update_key(self, entity):
        raw = {"rating": 4.5, "rating_count": 1000}
        metrics = AppStoreFetcher(entity).extract_metrics(raw)
        assert "appstore_last_update_days_ago" not in metrics


@pytest.mark.django_db
class TestAppStoreFetcherRun:
    @resp.activate
    def test_run_persists_metrics(self, entity):
        resp.add(resp.GET, ITUNES_URL, json=ITUNES_RESPONSE)
        AppStoreFetcher(entity).run()
        assert Metric.objects.filter(entity=entity, key="appstore_rating").exists()
        assert Metric.objects.filter(entity=entity, key="appstore_rating_count").exists()

    @resp.activate
    def test_run_does_not_crash_on_failure(self, entity):
        resp.add(resp.GET, ITUNES_URL, body=Exception("Connection error"))
        AppStoreFetcher(entity).run()  # must not raise
        assert Metric.objects.filter(entity=entity).count() == 0

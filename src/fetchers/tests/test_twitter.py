# fetchers/tests/test_twitter.py
"""Tests for TwitterFetcher. All HTTP calls are mocked via responses library."""

import pytest
import requests
import responses as resp
from django.test import override_settings

from core.tests.factories import EntityFactory
from fetchers.twitter import TwitterFetcher
from metrics.models import Metric, MetricSnapshot

TWITTER_SEARCH_URL = "https://api.twitter.com/2/tweets/search/recent"
HANDLE = "TestOrg"
BEARER_TOKEN = "test-bearer-token"

TWEET = {
    "id": "1234567890",
    "text": "Test tweet about TestOrg",
    "public_metrics": {
        "reply_count": 2,
        "retweet_count": 5,
        "like_count": 20,
        "quote_count": 1,
    },
}

TWITTER_RESPONSE = {
    "data": [TWEET, TWEET, TWEET],
    "meta": {"result_count": 3},
}


@pytest.fixture
def entity(db):
    return EntityFactory(twitter_handle=HANDLE)


@pytest.fixture
def entity_no_handle(db):
    return EntityFactory(twitter_handle="")


@pytest.mark.django_db
class TestTwitterFetcherFetch:
    def test_no_handle_returns_empty(self, entity_no_handle):
        data = TwitterFetcher(entity_no_handle).fetch()
        assert data == {}

    def test_no_bearer_token_returns_empty(self, entity):
        data = TwitterFetcher(entity).fetch()
        assert data == {}

    @override_settings(TWITTER_BEARER_TOKEN=BEARER_TOKEN)
    @resp.activate
    def test_happy_path_returns_tweets(self, entity):
        resp.add(resp.GET, TWITTER_SEARCH_URL, json=TWITTER_RESPONSE)
        data = TwitterFetcher(entity).fetch()
        assert len(data["tweets"]) == 3
        assert data["tweets"][0]["public_metrics"]["like_count"] == 20

    @override_settings(TWITTER_BEARER_TOKEN=BEARER_TOKEN)
    @resp.activate
    def test_api_failure_returns_empty(self, entity):
        resp.add(resp.GET, TWITTER_SEARCH_URL, status=503)
        data = TwitterFetcher(entity).fetch()
        assert data == {}

    @override_settings(TWITTER_BEARER_TOKEN=BEARER_TOKEN)
    @resp.activate
    def test_rate_limit_returns_empty(self, entity):
        resp.add(resp.GET, TWITTER_SEARCH_URL, status=429)
        data = TwitterFetcher(entity).fetch()
        assert data == {}

    @override_settings(TWITTER_BEARER_TOKEN=BEARER_TOKEN)
    @resp.activate
    def test_network_error_returns_empty(self, entity):
        resp.add(
            resp.GET,
            TWITTER_SEARCH_URL,
            body=requests.exceptions.ConnectionError("Connection refused"),
        )
        data = TwitterFetcher(entity).fetch()
        assert data == {}

    @override_settings(TWITTER_BEARER_TOKEN=BEARER_TOKEN)
    @resp.activate
    def test_no_data_key_returns_empty_tweets(self, entity):
        # API returns 200 but no "data" field (zero results)
        resp.add(resp.GET, TWITTER_SEARCH_URL, json={"meta": {"result_count": 0}})
        data = TwitterFetcher(entity).fetch()
        assert data == {"tweets": []}


@pytest.mark.django_db
class TestTwitterFetcherExtractMetrics:
    def test_extract_metrics_keys_and_values(self, entity):
        # 3 tweets × (2+5+20+1) = 3 × 28 = 84 total engagement
        raw = {"tweets": [TWEET, TWEET, TWEET]}
        metrics = TwitterFetcher(entity).extract_metrics(raw)
        assert metrics["twitter_mention_count"] == 3.0
        assert metrics["twitter_total_engagement"] == 84.0
        assert metrics["twitter_avg_engagement"] == 28.0

    def test_empty_tweets_returns_zeros(self, entity):
        metrics = TwitterFetcher(entity).extract_metrics({"tweets": []})
        assert metrics["twitter_mention_count"] == 0.0
        assert metrics["twitter_total_engagement"] == 0.0
        assert metrics["twitter_avg_engagement"] == 0.0

    def test_missing_public_metrics_does_not_crash(self, entity):
        # Tweet object with no public_metrics key
        raw = {"tweets": [{"id": "1", "text": "no metrics"}]}
        metrics = TwitterFetcher(entity).extract_metrics(raw)
        assert metrics["twitter_mention_count"] == 1.0
        assert metrics["twitter_total_engagement"] == 0.0
        assert metrics["twitter_avg_engagement"] == 0.0

    def test_partial_public_metrics_fields(self, entity):
        # Only some engagement fields present
        tweet = {"id": "1", "public_metrics": {"like_count": 10}}
        metrics = TwitterFetcher(entity).extract_metrics({"tweets": [tweet]})
        assert metrics["twitter_total_engagement"] == 10.0
        assert metrics["twitter_avg_engagement"] == 10.0


@pytest.mark.django_db
class TestTwitterFetcherRun:
    @override_settings(TWITTER_BEARER_TOKEN=BEARER_TOKEN)
    @resp.activate
    def test_run_persists_snapshot_and_metrics(self, entity):
        resp.add(resp.GET, TWITTER_SEARCH_URL, json=TWITTER_RESPONSE)
        TwitterFetcher(entity).run()
        assert MetricSnapshot.objects.filter(entity=entity, source="twitter").exists()
        assert Metric.objects.filter(entity=entity, key="twitter_mention_count").exists()
        assert Metric.objects.filter(entity=entity, key="twitter_total_engagement").exists()
        assert Metric.objects.filter(entity=entity, key="twitter_avg_engagement").exists()

    @override_settings(TWITTER_BEARER_TOKEN=BEARER_TOKEN)
    @resp.activate
    def test_run_does_not_crash_on_network_error(self, entity):
        resp.add(
            resp.GET,
            TWITTER_SEARCH_URL,
            body=requests.exceptions.ConnectionError("Connection error"),
        )
        TwitterFetcher(entity).run()  # must not raise
        assert Metric.objects.filter(entity=entity).count() == 0

    def test_run_does_not_crash_when_no_handle(self, entity_no_handle):
        TwitterFetcher(entity_no_handle).run()  # must not raise
        assert Metric.objects.filter(entity=entity_no_handle).count() == 0

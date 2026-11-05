# fetchers/tests/test_news.py
"""Tests for NewsFetcher. requests.get and feedparser.parse() are mocked to avoid real network calls."""

from unittest.mock import MagicMock, patch

import pytest

from core.tests.factories import EntityFactory
from fetchers.news import NewsFetcher
from metrics.models import Metric, NewsItem


def _mock_response():
    """Return a mock requests.Response with .content and .raise_for_status()."""
    resp = MagicMock()
    resp.content = b"<rss></rss>"
    resp.raise_for_status = MagicMock()
    return resp


# Simulated feedparser entry returned by feedparser.parse()
MOCK_ENTRY = MagicMock()
MOCK_ENTRY.get = lambda key, default=None: {
    "title": "Bitcoin Lightning Network Update",
    "link": "https://example.com/test-article",
    "published": "Mon, 01 Jan 2024 12:00:00 +0000",
    "source": {"title": "Test Source"},
}.get(key, default)


def _mock_feed(entries=None):
    feed = MagicMock()
    feed.entries = entries if entries is not None else [MOCK_ENTRY]
    return feed


@pytest.fixture
def entity(db):
    return EntityFactory(news_keywords=["bitcoin", "lightning"])


@pytest.fixture
def entity_no_keywords(db):
    return EntityFactory(news_keywords=[])


@pytest.mark.django_db
class TestNewsFetcherFetch:
    @patch("fetchers.news.requests.get", return_value=_mock_response())
    @patch("fetchers.news.feedparser.parse", return_value=_mock_feed())
    def test_happy_path_returns_articles(self, mock_parse, mock_get, entity):
        data = NewsFetcher(entity).fetch()
        assert "articles" in data
        assert len(data["articles"]) >= 1
        assert data["articles"][0]["title"] == "Bitcoin Lightning Network Update"

    def test_no_keywords_returns_empty(self, entity_no_keywords):
        data = NewsFetcher(entity_no_keywords).fetch()
        assert data == {}

    @patch("fetchers.news.requests.get", return_value=_mock_response())
    @patch("fetchers.news.feedparser.parse", return_value=_mock_feed(entries=[]))
    def test_empty_feed_returns_empty_articles(self, mock_parse, mock_get, entity):
        data = NewsFetcher(entity).fetch()
        assert data["articles"] == []

    @patch("fetchers.news.requests.get", return_value=_mock_response())
    @patch("fetchers.news.feedparser.parse", return_value=_mock_feed())
    def test_deduplicates_across_keywords(self, mock_parse, mock_get, entity):
        """Same URL from two keyword queries must appear only once."""
        data = NewsFetcher(entity).fetch()
        urls = [a["url"] for a in data["articles"]]
        assert len(urls) == len(set(urls))


@pytest.mark.django_db
class TestNewsFetcherExtractMetrics:
    def test_returns_article_count(self, entity):
        data = {"articles": [{"url": "a"}, {"url": "b"}]}
        metrics = NewsFetcher(entity).extract_metrics(data)
        assert metrics["news_article_count"] == 2.0

    def test_empty_articles_returns_zero(self, entity):
        metrics = NewsFetcher(entity).extract_metrics({"articles": []})
        assert metrics["news_article_count"] == 0.0


@pytest.mark.django_db
class TestNewsFetcherStore:
    @patch("fetchers.news.requests.get", return_value=_mock_response())
    @patch("fetchers.news.feedparser.parse", return_value=_mock_feed())
    def test_run_creates_news_items(self, mock_parse, mock_get, entity):
        NewsFetcher(entity).run()
        assert NewsItem.objects.filter(url="https://example.com/test-article").exists()

    @patch("fetchers.news.requests.get", return_value=_mock_response())
    @patch("fetchers.news.feedparser.parse", return_value=_mock_feed())
    def test_run_creates_metric_row(self, mock_parse, mock_get, entity):
        NewsFetcher(entity).run()
        assert Metric.objects.filter(entity=entity, key="news_article_count").exists()

    @patch("fetchers.news.requests.get", return_value=_mock_response())
    @patch("fetchers.news.feedparser.parse", return_value=_mock_feed())
    def test_run_idempotent_news_items(self, mock_parse, mock_get, entity):
        """Running twice must not create duplicate NewsItem rows."""
        NewsFetcher(entity).run()
        NewsFetcher(entity).run()
        assert NewsItem.objects.filter(url="https://example.com/test-article").count() == 1

    @patch("fetchers.news.requests.get", return_value=_mock_response())
    @patch("fetchers.news.feedparser.parse", return_value=_mock_feed())
    def test_run_tags_entity_to_news_item(self, mock_parse, mock_get, entity):
        NewsFetcher(entity).run()
        item = NewsItem.objects.get(url="https://example.com/test-article")
        assert entity in item.entities.all()

# fetchers/tests/test_reddit.py
"""Tests for RedditFetcher. All HTTP calls are mocked via responses library."""

import pytest
import requests
import responses as resp

from core.tests.factories import EntityFactory
from fetchers.reddit import MAX_PAGES, MAX_RESULTS_PER_PAGE, RedditFetcher
from metrics.models import Metric, MetricSnapshot

REDDIT_SEARCH_URL = "https://www.reddit.com/search.json"
KEYWORDS = ["Test Keyword", "Test Product"]


def _make_post(title: str = "Test post") -> dict:
    """Return a minimal Reddit post child object."""
    return {"kind": "t3", "data": {"title": title, "subreddit": "Bitcoin", "score": 10}}


def _make_response(posts: list, after: str | None = None) -> dict:
    """Wrap posts in the Reddit JSON API envelope."""
    return {"data": {"children": posts, "after": after, "before": None}}


@pytest.fixture
def entity(db):
    return EntityFactory(reddit_keywords=KEYWORDS)


@pytest.fixture
def entity_no_keywords(db):
    return EntityFactory(reddit_keywords=[])


@pytest.mark.django_db
class TestRedditFetcherFetch:
    def test_no_keywords_returns_empty(self, entity_no_keywords):
        data = RedditFetcher(entity_no_keywords).fetch()
        assert data == {}

    @resp.activate
    def test_happy_path_returns_posts(self, entity):
        posts = [_make_post(f"Post {i}") for i in range(5)]
        resp.add(resp.GET, REDDIT_SEARCH_URL, json=_make_response(posts, after=None))
        data = RedditFetcher(entity).fetch()
        assert len(data["posts"]) == 5

    @resp.activate
    def test_pagination_follows_after_cursor(self, entity):
        page1 = [_make_post(f"Post {i}") for i in range(MAX_RESULTS_PER_PAGE)]
        page2 = [_make_post("Last post")]
        resp.add(resp.GET, REDDIT_SEARCH_URL, json=_make_response(page1, after="t3_abc"))
        resp.add(resp.GET, REDDIT_SEARCH_URL, json=_make_response(page2, after=None))
        data = RedditFetcher(entity).fetch()
        assert len(data["posts"]) == MAX_RESULTS_PER_PAGE + 1

    @resp.activate
    def test_pagination_caps_at_max_pages(self, entity):
        # Every page is full and returns an "after" cursor — should stop at MAX_PAGES
        full_page = [_make_post(f"Post {i}") for i in range(MAX_RESULTS_PER_PAGE)]
        for _ in range(MAX_PAGES):
            resp.add(resp.GET, REDDIT_SEARCH_URL, json=_make_response(full_page, after="t3_xyz"))
        data = RedditFetcher(entity).fetch()
        assert len(data["posts"]) == MAX_PAGES * MAX_RESULTS_PER_PAGE

    @resp.activate
    def test_api_failure_returns_empty(self, entity):
        resp.add(resp.GET, REDDIT_SEARCH_URL, status=500)
        data = RedditFetcher(entity).fetch()
        assert data == {"posts": []}

    @resp.activate
    def test_rate_limit_returns_partial_results(self, entity):
        # First page succeeds, second returns 429 — should return first page's posts
        page1 = [_make_post(f"Post {i}") for i in range(MAX_RESULTS_PER_PAGE)]
        resp.add(resp.GET, REDDIT_SEARCH_URL, json=_make_response(page1, after="t3_abc"))
        resp.add(resp.GET, REDDIT_SEARCH_URL, status=429)
        data = RedditFetcher(entity).fetch()
        assert len(data["posts"]) == MAX_RESULTS_PER_PAGE

    @resp.activate
    def test_network_error_returns_partial_results(self, entity):
        resp.add(
            resp.GET,
            REDDIT_SEARCH_URL,
            body=requests.exceptions.ConnectionError("Connection refused"),
        )
        data = RedditFetcher(entity).fetch()
        assert data == {"posts": []}

    @resp.activate
    def test_non_t3_children_are_filtered_out(self, entity):
        # Mix of post (t3) and "more" objects — only t3 should be counted
        children = [
            {"kind": "t3", "data": {"title": "Real post"}},
            {"kind": "more", "data": {"count": 5}},
        ]
        resp.add(resp.GET, REDDIT_SEARCH_URL, json=_make_response(children, after=None))
        data = RedditFetcher(entity).fetch()
        assert len(data["posts"]) == 1


@pytest.mark.django_db
class TestRedditFetcherExtractMetrics:
    def test_returns_mention_count(self, entity):
        raw = {"posts": [{}, {}, {}]}
        metrics = RedditFetcher(entity).extract_metrics(raw)
        assert metrics["reddit_mention_count"] == 3.0

    def test_empty_posts_returns_zero(self, entity):
        metrics = RedditFetcher(entity).extract_metrics({"posts": []})
        assert metrics["reddit_mention_count"] == 0.0

    def test_missing_posts_key_returns_zero(self, entity):
        metrics = RedditFetcher(entity).extract_metrics({})
        assert metrics["reddit_mention_count"] == 0.0


@pytest.mark.django_db
class TestRedditFetcherRun:
    @resp.activate
    def test_run_persists_snapshot_and_metrics(self, entity):
        posts = [_make_post(f"Post {i}") for i in range(3)]
        resp.add(resp.GET, REDDIT_SEARCH_URL, json=_make_response(posts, after=None))
        RedditFetcher(entity).run()
        assert MetricSnapshot.objects.filter(entity=entity, source="reddit").exists()
        assert Metric.objects.filter(entity=entity, key="reddit_mention_count").exists()

    @resp.activate
    def test_run_does_not_crash_on_network_error(self, entity):
        # Network failure on first page → fetch() returns {"posts": []} → stores 0.0
        resp.add(
            resp.GET,
            REDDIT_SEARCH_URL,
            body=requests.exceptions.ConnectionError("Connection error"),
        )
        RedditFetcher(entity).run()  # must not raise
        metric = Metric.objects.filter(entity=entity, key="reddit_mention_count").first()
        assert metric is not None
        assert metric.value == 0.0

    def test_run_does_not_crash_when_no_keywords(self, entity_no_keywords):
        RedditFetcher(entity_no_keywords).run()  # must not raise
        assert Metric.objects.filter(entity=entity_no_keywords).count() == 0

# fetchers/tests/test_github.py
"""Tests for GithubFetcher. All HTTP calls are mocked via responses library."""

import pytest
import responses as resp

from core.tests.factories import EntityFactory
from fetchers.github import GithubFetcher
from metrics.models import Metric, MetricSnapshot

GITHUB_ORG = "test-org"
GITHUB_REPO = "test-repo"
COMMITS_URL = f"https://api.github.com/repos/{GITHUB_ORG}/{GITHUB_REPO}/commits"
CONTRIBUTORS_URL = f"https://api.github.com/repos/{GITHUB_ORG}/{GITHUB_REPO}/contributors"

ORG_RESPONSE = {"login": GITHUB_ORG, "public_repos": 12}
REPO_RESPONSE = {
    "full_name": f"{GITHUB_ORG}/{GITHUB_REPO}",
    "stargazers_count": 500,
    "forks_count": 80,
    "open_issues_count": 15,
}
RELEASE_RESPONSE = {"published_at": "2024-01-01T00:00:00Z", "tag_name": "v1.0.0"}
# Minimal commit object; real response has many fields but we only care about count
COMMIT_ITEM = {"sha": "abc123", "commit": {"message": "fix: something"}}
COMMITS_RESPONSE = [COMMIT_ITEM] * 3  # 3 commits in window
# Minimal contributor objects; only the count matters
CONTRIBUTOR_ITEM = {"login": "user", "contributions": 1}
CONTRIBUTORS_RESPONSE = [CONTRIBUTOR_ITEM] * 7  # 7 contributors


@pytest.fixture
def entity(db):
    return EntityFactory(github_org=GITHUB_ORG, github_repo=GITHUB_REPO)


@pytest.fixture
def entity_no_config(db):
    return EntityFactory(github_org="", github_repo="")


def _mock_all_endpoints(include_commits: bool = True) -> None:
    """Register mock responses for all GitHub endpoints used by GithubFetcher."""
    resp.add(resp.GET, f"https://api.github.com/orgs/{GITHUB_ORG}", json=ORG_RESPONSE)
    resp.add(
        resp.GET,
        f"https://api.github.com/repos/{GITHUB_ORG}/{GITHUB_REPO}",
        json=REPO_RESPONSE,
    )
    resp.add(
        resp.GET,
        f"https://api.github.com/repos/{GITHUB_ORG}/{GITHUB_REPO}/releases/latest",
        json=RELEASE_RESPONSE,
    )
    # Contributors endpoint — called once per fetch cycle
    resp.add(resp.GET, CONTRIBUTORS_URL, json=CONTRIBUTORS_RESPONSE)
    if include_commits:
        # Two commit windows (30d + 90d), each returns 3 commits.
        # 3 < 100 so _count_commits exits after page 1 — no empty page needed.
        resp.add(resp.GET, COMMITS_URL, json=COMMITS_RESPONSE)
        resp.add(resp.GET, COMMITS_URL, json=COMMITS_RESPONSE)


@pytest.mark.django_db
class TestGithubFetcherFetch:
    @resp.activate
    def test_happy_path_returns_all_fields(self, entity):
        _mock_all_endpoints()

        data = GithubFetcher(entity).fetch()

        assert data["stargazers_count"] == 500
        assert data["forks_count"] == 80
        assert data["open_issues_count"] == 15
        assert data["public_repos"] == 12
        assert data["contributors"] == 7
        assert "latest_release_published_at" in data
        assert data["commits_30d"] == 3
        assert data["commits_90d"] == 3

    def test_no_github_org_returns_empty(self, entity_no_config):
        data = GithubFetcher(entity_no_config).fetch()
        assert data == {}

    @resp.activate
    def test_api_failure_returns_none_for_missing_fields(self, entity):
        """Org endpoint fails → public_repos not in payload; repo endpoint fails → no stars."""
        resp.add(resp.GET, f"https://api.github.com/orgs/{GITHUB_ORG}", status=503)
        resp.add(
            resp.GET,
            f"https://api.github.com/repos/{GITHUB_ORG}/{GITHUB_REPO}",
            status=503,
        )
        resp.add(
            resp.GET,
            f"https://api.github.com/repos/{GITHUB_ORG}/{GITHUB_REPO}/releases/latest",
            status=503,
        )
        resp.add(resp.GET, CONTRIBUTORS_URL, status=503)
        resp.add(resp.GET, COMMITS_URL, json=[])
        resp.add(resp.GET, COMMITS_URL, json=[])

        data = GithubFetcher(entity).fetch()
        # Partial failures return empty payload (no crashes)
        assert isinstance(data, dict)

    @resp.activate
    def test_404_org_does_not_crash(self, entity):
        resp.add(resp.GET, f"https://api.github.com/orgs/{GITHUB_ORG}", status=404)
        resp.add(
            resp.GET,
            f"https://api.github.com/repos/{GITHUB_ORG}/{GITHUB_REPO}",
            json=REPO_RESPONSE,
        )
        resp.add(
            resp.GET,
            f"https://api.github.com/repos/{GITHUB_ORG}/{GITHUB_REPO}/releases/latest",
            json=RELEASE_RESPONSE,
        )
        resp.add(resp.GET, CONTRIBUTORS_URL, json=CONTRIBUTORS_RESPONSE)
        resp.add(resp.GET, COMMITS_URL, json=COMMITS_RESPONSE)
        resp.add(resp.GET, COMMITS_URL, json=COMMITS_RESPONSE)
        data = GithubFetcher(entity).fetch()
        assert "public_repos" not in data
        assert data["stargazers_count"] == 500


@pytest.mark.django_db
class TestGithubFetcherExtractMetrics:
    def test_extract_metrics_keys(self, entity):
        raw = {
            "stargazers_count": 100,
            "forks_count": 20,
            "open_issues_count": 5,
            "public_repos": 8,
            "contributors": 42,
            "latest_release_published_at": "2024-01-01T00:00:00Z",
            "commits_30d": 42,
            "commits_90d": 130,
        }
        metrics = GithubFetcher(entity).extract_metrics(raw)
        assert "github_stars" in metrics
        assert "github_forks" in metrics
        assert "github_open_issues" in metrics
        assert "github_public_repos" in metrics
        assert "github_contributors" in metrics
        assert "github_latest_release_days_ago" in metrics
        assert metrics["github_stars"] == 100.0
        assert metrics["github_contributors"] == 42.0
        assert metrics["github_commits_30d"] == 42.0
        assert metrics["github_commits_90d"] == 130.0

    def test_missing_fields_produce_no_keys(self, entity):
        metrics = GithubFetcher(entity).extract_metrics({})
        assert metrics == {}

    def test_commit_frequency_absent_when_not_in_payload(self, entity):
        raw = {"stargazers_count": 10}
        metrics = GithubFetcher(entity).extract_metrics(raw)
        assert "github_commits_30d" not in metrics
        assert "github_commits_90d" not in metrics


@pytest.mark.django_db
class TestGithubFetcherRun:
    @resp.activate
    def test_run_persists_snapshot_and_metrics(self, entity):
        _mock_all_endpoints()

        GithubFetcher(entity).run()

        assert MetricSnapshot.objects.filter(entity=entity, source="github").exists()
        assert Metric.objects.filter(entity=entity, key="github_stars").exists()
        assert Metric.objects.filter(entity=entity, key="github_contributors").exists()
        assert Metric.objects.filter(entity=entity, key="github_commits_30d").exists()
        assert Metric.objects.filter(entity=entity, key="github_commits_90d").exists()

    @resp.activate
    def test_run_does_not_crash_on_api_failure(self, entity):
        """A complete network failure must be swallowed by run()."""
        resp.add(
            resp.GET,
            f"https://api.github.com/orgs/{GITHUB_ORG}",
            body=Exception("Network error"),
        )
        resp.add(
            resp.GET,
            f"https://api.github.com/repos/{GITHUB_ORG}/{GITHUB_REPO}",
            body=Exception("Network error"),
        )
        resp.add(
            resp.GET,
            f"https://api.github.com/repos/{GITHUB_ORG}/{GITHUB_REPO}/releases/latest",
            body=Exception("Network error"),
        )
        resp.add(resp.GET, CONTRIBUTORS_URL, body=Exception("Network error"))
        resp.add(resp.GET, COMMITS_URL, body=Exception("Network error"))
        resp.add(resp.GET, COMMITS_URL, body=Exception("Network error"))
        # Must not raise
        GithubFetcher(entity).run()
        assert MetricSnapshot.objects.filter(entity=entity).count() == 0

    @resp.activate
    def test_count_commits_caps_at_max_pages(self, entity):
        """_count_commits stops after MAX_COMMIT_PAGES pages (500 commit cap)."""
        from fetchers.github import MAX_COMMIT_PAGES

        repo_slug = f"{GITHUB_ORG}/{GITHUB_REPO}"
        # Register MAX_COMMIT_PAGES + 1 full pages (100 items each)
        full_page = [COMMIT_ITEM] * 100
        for _ in range(MAX_COMMIT_PAGES + 1):
            resp.add(resp.GET, COMMITS_URL, json=full_page)

        from datetime import datetime, timedelta, timezone

        since = datetime.now(tz=timezone.utc) - timedelta(days=30)
        fetcher = GithubFetcher(entity)
        count = fetcher._count_commits(repo_slug, since=since)

        assert count == MAX_COMMIT_PAGES * 100

    @resp.activate
    def test_count_contributors_returns_correct_count(self, entity):
        """_count_contributors counts items returned by the contributors endpoint."""
        repo_slug = f"{GITHUB_ORG}/{GITHUB_REPO}"
        resp.add(resp.GET, CONTRIBUTORS_URL, json=CONTRIBUTORS_RESPONSE)

        fetcher = GithubFetcher(entity)
        count = fetcher._count_contributors(repo_slug)

        assert count == len(CONTRIBUTORS_RESPONSE)

    @resp.activate
    def test_count_contributors_404_returns_none(self, entity):
        """A 404 on the contributors endpoint returns None gracefully."""
        repo_slug = f"{GITHUB_ORG}/{GITHUB_REPO}"
        resp.add(resp.GET, CONTRIBUTORS_URL, status=404)

        fetcher = GithubFetcher(entity)
        count = fetcher._count_contributors(repo_slug)

        assert count is None

    @resp.activate
    def test_count_contributors_403_returns_none(self, entity):
        """A 403 (e.g. too-large repo) on the contributors endpoint returns None gracefully."""
        repo_slug = f"{GITHUB_ORG}/{GITHUB_REPO}"
        resp.add(resp.GET, CONTRIBUTORS_URL, status=403)

        fetcher = GithubFetcher(entity)
        count = fetcher._count_contributors(repo_slug)

        assert count is None

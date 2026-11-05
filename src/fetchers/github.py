# fetchers/github.py
"""
GitHub fetcher.

Collects public metrics for a tracked entity's GitHub org and primary repo.
Uses the public GitHub REST API (no auth required; token recommended for higher rate limits).

Metrics produced:
    github_stars                   — stargazers on the primary repo
    github_forks                   — forks on the primary repo
    github_open_issues             — open issues on the primary repo
    github_public_repos            — total public repos for the org
    github_contributors            — number of contributors to the primary repo
    github_latest_release_days_ago — days since the latest release was published
    github_commits_30d             — commit count on default branch in the last 30 days
    github_commits_90d             — commit count on default branch in the last 90 days
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from django.conf import settings

from core.models import Entity
from fetchers.base import BaseFetcher
from fetchers.utils import parse_date, to_float

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"
REQUEST_TIMEOUT = 10  # seconds

# Safety cap: stop paginating after this many pages to avoid excessive API calls.
# At 100 commits/page this caps commit counts at 500 per window.
MAX_COMMIT_PAGES = 5


class GithubFetcher(BaseFetcher):
    """Fetches GitHub metrics for an entity's org and primary repo."""

    source_name = "github"

    def __init__(self, entity: Entity) -> None:
        super().__init__(entity)
        # Build auth headers if a token is configured
        token = getattr(settings, "GITHUB_TOKEN", "")
        self._headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
        if token:
            self._headers["Authorization"] = f"Bearer {token}"

    # ------------------------------------------------------------------
    # fetch
    # ------------------------------------------------------------------

    def fetch(self) -> dict[str, Any]:
        """
        Pull org-level and repo-level data from the GitHub REST API.
        Returns an empty dict if the entity has no github_org configured.
        """
        if not self.entity.github_org:
            logger.debug("Entity '%s' has no github_org — skipping.", self.entity.name)
            return {}

        payload: dict[str, Any] = {}

        # --- Org-level: public repo count ---
        org_data = self._get(f"{GITHUB_API_BASE}/orgs/{self.entity.github_org}")
        if org_data:
            payload["public_repos"] = org_data.get("public_repos")

        # --- Repo-level: stars, forks, issues, release, commit frequency ---
        if self.entity.github_repo:
            repo_slug = f"{self.entity.github_org}/{self.entity.github_repo}"
            repo_data = self._get(f"{GITHUB_API_BASE}/repos/{repo_slug}")
            if repo_data:
                payload["stargazers_count"] = repo_data.get("stargazers_count")
                payload["forks_count"] = repo_data.get("forks_count")
                payload["open_issues_count"] = repo_data.get("open_issues_count")

            # --- Latest release date ---
            release_data = self._get(f"{GITHUB_API_BASE}/repos/{repo_slug}/releases/latest")
            if release_data and release_data.get("published_at"):
                payload["latest_release_published_at"] = release_data["published_at"]
                payload["latest_release_tag"] = release_data.get("tag_name", "")

            # --- Contributor count ---
            # Uses per_page=100 and counts list items; accurate for repos with <1000 contributors.
            # anon=true includes anonymous contributors (email-only) in the total.
            contributor_count = self._count_contributors(repo_slug)
            if contributor_count is not None:
                payload["contributors"] = contributor_count

            # --- Commit frequency: count commits in last 30 and 90 days ---
            now = datetime.now(tz=timezone.utc)
            payload["commits_30d"] = self._count_commits(repo_slug, since=now - timedelta(days=30))
            payload["commits_90d"] = self._count_commits(repo_slug, since=now - timedelta(days=90))

        return payload

    # ------------------------------------------------------------------
    # extract_metrics
    # ------------------------------------------------------------------

    def extract_metrics(self, data: dict[str, Any]) -> dict[str, float | None]:
        """Map raw GitHub payload to flat metric key/value pairs."""
        metrics: dict[str, float | None] = {}

        # Direct numeric mappings
        if "stargazers_count" in data:
            metrics["github_stars"] = to_float(data["stargazers_count"])
        if "forks_count" in data:
            metrics["github_forks"] = to_float(data["forks_count"])
        if "open_issues_count" in data:
            metrics["github_open_issues"] = to_float(data["open_issues_count"])
        if "public_repos" in data:
            metrics["github_public_repos"] = to_float(data["public_repos"])
        if "contributors" in data:
            metrics["github_contributors"] = to_float(data["contributors"])

        # Days since latest release (allows delta detection to catch stale releases)
        if "latest_release_published_at" in data:
            published = parse_date(data["latest_release_published_at"])
            if published:
                delta = datetime.now(tz=timezone.utc) - published
                metrics["github_latest_release_days_ago"] = float(delta.days)

        # Commit frequency windows
        if "commits_30d" in data:
            metrics["github_commits_30d"] = to_float(data["commits_30d"])
        if "commits_90d" in data:
            metrics["github_commits_90d"] = to_float(data["commits_90d"])

        return metrics

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _count_contributors(self, repo_slug: str) -> int | None:
        """
        Count total contributors to the repo using the GitHub contributors endpoint.

        Fetches up to 100 contributors per page (the API maximum). Accurate for
        repos with fewer than 1000 contributors, which covers all tracked entities.
        anon=true includes contributors identified only by email address.

        Returns None if the endpoint returns 404 (empty/missing repo).
        """
        url = f"{GITHUB_API_BASE}/repos/{repo_slug}/contributors"
        params = {"per_page": 100, "anon": "true"}
        try:
            response = requests.get(
                url, headers=self._headers, params=params, timeout=REQUEST_TIMEOUT
            )
            if response.status_code == 404:
                logger.debug("GitHub 404 for contributors on %s", repo_slug)
                return None
            # 403 can occur on forks with many contributors — log and skip rather than crash
            if response.status_code == 403:
                logger.warning(
                    "GitHub 403 for contributors on %s (likely too many — skipping)", repo_slug
                )
                return None
            response.raise_for_status()
            try:
                contributors = response.json()
            except ValueError as exc:
                logger.warning("GitHub contributors invalid JSON for %s: %s", repo_slug, exc)
                return None
        except requests.RequestException as exc:
            logger.warning("GitHub contributors request failed for %s: %s", repo_slug, exc)
            return None

        if not isinstance(contributors, list):
            return None
        return len(contributors)

    def _count_commits(self, repo_slug: str, since: datetime) -> int:
        """
        Count commits on the default branch since a given UTC datetime.

        Paginates the /repos/{owner}/{repo}/commits endpoint at 100 per page.
        Stops after MAX_COMMIT_PAGES pages to avoid hammering the API for
        very active repos. The result is therefore a lower-bound count when
        commits exceed MAX_COMMIT_PAGES * 100.
        """
        since_iso = since.strftime("%Y-%m-%dT%H:%M:%SZ")
        count = 0

        for page in range(1, MAX_COMMIT_PAGES + 1):
            url = f"{GITHUB_API_BASE}/repos/{repo_slug}/commits"
            params = {"since": since_iso, "per_page": 100, "page": page}
            try:
                response = requests.get(
                    url, headers=self._headers, params=params, timeout=REQUEST_TIMEOUT
                )
                if response.status_code == 404:
                    logger.debug("GitHub 404 for commits on %s", repo_slug)
                    break
                response.raise_for_status()
                try:
                    page_items = response.json()
                except ValueError as exc:
                    logger.warning("GitHub commits invalid JSON for %s: %s", repo_slug, exc)
                    break
            except requests.RequestException as exc:
                logger.warning("GitHub commits request failed for %s: %s", repo_slug, exc)
                break

            if not isinstance(page_items, list) or not page_items:
                # Empty page means we've reached the end
                break

            count += len(page_items)

            # If the page returned fewer than 100, this was the last page
            if len(page_items) < 100:
                break

        return count

    def _get(self, url: str) -> dict[str, Any] | None:
        """GET a GitHub API URL, return parsed JSON or None on failure."""
        try:
            response = requests.get(url, headers=self._headers, timeout=REQUEST_TIMEOUT)
            if response.status_code == 404:
                logger.debug("GitHub 404 for %s", url)
                return None
            response.raise_for_status()
            try:
                return response.json()
            except ValueError as exc:
                logger.warning("GitHub invalid JSON from %s: %s", url, exc)
                return None
        except requests.RequestException as exc:
            logger.warning("GitHub request failed for %s: %s", url, exc)
            return None

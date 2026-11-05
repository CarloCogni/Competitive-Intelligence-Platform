# fetchers/reddit.py
"""
Reddit fetcher.

Searches Reddit's public JSON API for posts mentioning the entity's
configured keywords and extracts one metric:

    reddit_mention_count — number of posts found in the last 7 days

No authentication is required; Reddit's public API accepts read requests
with a descriptive User-Agent header.

Pagination: up to MAX_PAGES pages of MAX_RESULTS_PER_PAGE posts are
fetched using Reddit's cursor-based ``after`` parameter.  The loop stops
early when Reddit returns no ``after`` cursor or a partial final page.
"""

import logging
from typing import Any

import requests

from core.models import Entity
from fetchers.base import BaseFetcher

logger = logging.getLogger(__name__)

REDDIT_SEARCH_URL = "https://www.reddit.com/search.json"
# Reddit requires a non-generic User-Agent; requests without one are rate-limited
REDDIT_USER_AGENT = "BlockStreamBI/1.0 (competitive intelligence)"
REQUEST_TIMEOUT = 10
MAX_RESULTS_PER_PAGE = 100
MAX_PAGES = 5  # cap total results at 500 posts per run


class RedditFetcher(BaseFetcher):
    """Fetch Reddit mention count for an entity by searching public posts."""

    source_name = "reddit"

    def __init__(self, entity: Entity) -> None:
        """Initialise with entity and build request headers."""
        super().__init__(entity)
        self._headers = {"User-Agent": REDDIT_USER_AGENT}

    def fetch(self) -> dict[str, Any]:
        """
        Search Reddit for posts matching the entity's reddit_keywords.

        Combines all keywords with OR into a single query so all relevant
        terms are searched in one request series.

        Returns a dict with a single key ``posts`` (list of post data dicts),
        or {} if no keywords are configured.
        """
        keywords: list[str] = self.entity.reddit_keywords
        if not keywords:
            logger.debug(
                "Reddit fetcher: no reddit_keywords for '%s' — skipping.",
                self.entity.name,
            )
            return {}

        # Join all keywords with OR so a single query covers all variants
        query = " OR ".join(keywords)
        posts: list[dict[str, Any]] = []
        after: str | None = None

        for _ in range(MAX_PAGES):
            params: dict[str, Any] = {
                "q": query,
                "sort": "new",
                "t": "week",
                "limit": MAX_RESULTS_PER_PAGE,
            }
            if after is not None:
                params["after"] = after

            try:
                response = requests.get(
                    REDDIT_SEARCH_URL,
                    headers=self._headers,
                    params=params,
                    timeout=REQUEST_TIMEOUT,
                )
            except requests.RequestException as exc:
                logger.warning(
                    "Reddit fetcher: request failed for '%s': %s",
                    self.entity.name,
                    exc,
                )
                break

            # Handle rate limit gracefully — stop pagination but keep results so far
            if response.status_code == 429:
                logger.warning(
                    "Reddit fetcher: rate limit hit for '%s' — stopping pagination.",
                    self.entity.name,
                )
                break

            try:
                response.raise_for_status()
            except requests.HTTPError as exc:
                logger.warning(
                    "Reddit fetcher: HTTP error for '%s': %s",
                    self.entity.name,
                    exc,
                )
                break

            try:
                payload = response.json()
            except ValueError as exc:
                logger.warning(
                    "Reddit fetcher: invalid JSON for '%s': %s",
                    self.entity.name,
                    exc,
                )
                break
            data = payload.get("data", {})
            children = data.get("children", [])

            if not children:
                break

            # kind=="t3" means a post (link/text); filter out any other object types
            posts.extend(child["data"] for child in children if child.get("kind") == "t3")

            after = data.get("after")
            if after is None:
                # No more pages available
                break

            if len(children) < MAX_RESULTS_PER_PAGE:
                # Partial page — this was the last page
                break

        return {"posts": posts}

    def extract_metrics(self, data: dict[str, Any]) -> dict[str, float | None]:
        """Return the total post count as the single Reddit metric."""
        count = len(data.get("posts", []))
        return {"reddit_mention_count": float(count)}

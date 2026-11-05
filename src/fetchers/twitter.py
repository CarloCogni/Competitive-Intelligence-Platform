# fetchers/twitter.py
"""
Twitter/X fetcher.

Queries the Twitter v2 recent-search endpoint for tweets mentioning
the entity's configured handle and extracts three metrics:

    twitter_mention_count     — number of matching tweets in the last 7 days
    twitter_total_engagement  — sum of likes + retweets + replies + quotes
    twitter_avg_engagement    — total_engagement / mention_count (0 if no tweets)

Requires TWITTER_BEARER_TOKEN in .env (free developer account).
If the token is absent or the entity has no twitter_handle configured,
fetch() returns {} and nothing is stored.

Free-tier rate limit: 1 request per 15 minutes per app token.
The scheduler registers this job at a 1-hour interval; 429 responses
are handled gracefully (return {}, log a warning).
"""

import logging
from typing import Any

import requests
from django.conf import settings

from core.models import Entity
from fetchers.base import BaseFetcher
from fetchers.utils import to_float

logger = logging.getLogger(__name__)

TWITTER_SEARCH_URL = "https://api.twitter.com/2/tweets/search/recent"
REQUEST_TIMEOUT = 10
MAX_RESULTS = 100


class TwitterFetcher(BaseFetcher):
    """Fetch Twitter/X mention count and engagement metrics for an entity."""

    source_name = "twitter"

    def __init__(self, entity: Entity) -> None:
        """Initialise with entity and read Bearer token from settings."""
        super().__init__(entity)
        self._bearer_token: str = getattr(settings, "TWITTER_BEARER_TOKEN", "")

    def fetch(self) -> dict[str, Any]:
        """
        Query Twitter v2 recent-search for tweets mentioning the entity's handle.

        Returns a dict with a single key ``tweets`` (list of tweet objects
        each containing a ``public_metrics`` sub-dict), or {} when config
        is missing or a 429 rate-limit is hit. Raises on other HTTP errors
        (e.g. 402 Payment Required) so the scheduler records a real failure.
        """
        if not self.entity.twitter_handle:
            logger.debug(
                "Twitter fetcher: no twitter_handle for '%s' — skipping.",
                self.entity.name,
            )
            return {}

        if not self._bearer_token:
            logger.warning(
                "Twitter fetcher: TWITTER_BEARER_TOKEN not set — skipping '%s'.",
                self.entity.name,
            )
            return {}

        headers = {"Authorization": f"Bearer {self._bearer_token}"}
        params = {
            "query": self.entity.twitter_handle,
            "tweet.fields": "public_metrics",
            "max_results": MAX_RESULTS,
        }

        try:
            response = requests.get(
                TWITTER_SEARCH_URL,
                headers=headers,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            logger.warning(
                "Twitter fetcher: request failed for '%s': %s",
                self.entity.name,
                exc,
            )
            return {}

        # Handle rate limit explicitly so it's logged as a warning, not an error
        if response.status_code == 429:
            logger.warning(
                "Twitter fetcher: rate limit hit for '%s' — will retry next cycle.",
                self.entity.name,
            )
            return {}

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            logger.warning(
                "Twitter fetcher: HTTP error for '%s': %s",
                self.entity.name,
                exc,
            )
            return {}

        try:
            payload = response.json()
        except ValueError as exc:
            logger.warning(
                "Twitter fetcher: invalid JSON response for '%s': %s",
                self.entity.name,
                exc,
            )
            return {}
        tweets: list[dict[str, Any]] = payload.get("data", [])
        return {"tweets": tweets}

    def extract_metrics(self, data: dict[str, Any]) -> dict[str, float | None]:
        """
        Derive flat numeric metrics from the raw tweet payload.

        Engagement = likes + retweets + replies + quotes per tweet, summed.
        Average engagement is 0.0 when there are no matching tweets.
        """
        tweets = data.get("tweets", [])
        mention_count = len(tweets)

        total_engagement = 0.0
        for tweet in tweets:
            pm = tweet.get("public_metrics", {})
            # Sum all four engagement signals; default to 0 if a field is absent
            total_engagement += (
                (pm.get("like_count") or 0)
                + (pm.get("retweet_count") or 0)
                + (pm.get("reply_count") or 0)
                + (pm.get("quote_count") or 0)
            )

        avg_engagement = total_engagement / mention_count if mention_count else 0.0

        return {
            "twitter_mention_count": to_float(mention_count),
            "twitter_total_engagement": to_float(total_engagement),
            "twitter_avg_engagement": to_float(avg_engagement),
        }

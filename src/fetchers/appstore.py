# fetchers/appstore.py
"""
App Store fetcher.

Uses the iTunes Search API (free, no authentication required) to pull
iOS App Store metrics for tracked entities.

Metrics produced:
    appstore_rating              — average user rating (current version)
    appstore_rating_count        — total number of ratings
    appstore_last_update_days_ago — days since the current version was released

Note: app version string (e.g. "2.1.0") is stored in the raw MetricSnapshot
payload under the key "version" but is not emitted as a flat Metric because
it is not numeric.
"""

import logging
from datetime import datetime, timezone
from typing import Any

import requests

from core.models import Entity
from fetchers.base import BaseFetcher
from fetchers.utils import parse_date, to_float

logger = logging.getLogger(__name__)

ITUNES_LOOKUP_URL = "https://itunes.apple.com/lookup"
REQUEST_TIMEOUT = 10  # seconds


class AppStoreFetcher(BaseFetcher):
    """Fetches iOS App Store metrics via the iTunes Search API."""

    source_name = "appstore"

    def __init__(self, entity: Entity) -> None:
        super().__init__(entity)

    # ------------------------------------------------------------------
    # fetch
    # ------------------------------------------------------------------

    def fetch(self) -> dict[str, Any]:
        """
        Query the iTunes lookup endpoint by App Store ID.
        Returns an empty dict if the entity has no app_store_id configured.
        """
        if not self.entity.app_store_id:
            logger.debug("Entity '%s' has no app_store_id — skipping.", self.entity.name)
            return {}

        params = {"id": self.entity.app_store_id, "country": "us"}
        try:
            response = requests.get(ITUNES_LOOKUP_URL, params=params, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.warning(
                "App Store request failed for '%s' (id=%s): %s",
                self.entity.name,
                self.entity.app_store_id,
                exc,
            )
            return {}
        try:
            data = response.json()
        except ValueError as exc:
            logger.warning(
                "App Store invalid JSON for '%s' (id=%s): %s",
                self.entity.name,
                self.entity.app_store_id,
                exc,
            )
            return {}

        results = data.get("results", [])
        if not results:
            logger.debug(
                "No App Store results for '%s' (id=%s).",
                self.entity.name,
                self.entity.app_store_id,
            )
            return {}

        # Take the first result (lookup by ID always returns at most one)
        app = results[0]
        return {
            "app_name": app.get("trackName", ""),
            "rating": app.get("averageUserRating"),
            "rating_count": app.get("userRatingCount"),
            # version string stored in raw snapshot only (not a numeric metric)
            "version": app.get("version", ""),
            "current_version_release_date": app.get("currentVersionReleaseDate", ""),
        }

    # ------------------------------------------------------------------
    # extract_metrics
    # ------------------------------------------------------------------

    def extract_metrics(self, data: dict[str, Any]) -> dict[str, float | None]:
        """Map App Store payload to flat metric key/value pairs."""
        metrics: dict[str, float | None] = {}

        if data.get("rating") is not None:
            metrics["appstore_rating"] = to_float(data["rating"])
        if data.get("rating_count") is not None:
            metrics["appstore_rating_count"] = to_float(data["rating_count"])

        # Days since current version was released (enables staleness delta detection)
        if data.get("current_version_release_date"):
            published = parse_date(data["current_version_release_date"])
            if published:
                delta = datetime.now(tz=timezone.utc) - published
                metrics["appstore_last_update_days_ago"] = float(delta.days)

        return metrics

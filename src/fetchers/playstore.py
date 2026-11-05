# fetchers/playstore.py
"""
Google Play Store fetcher.

Uses the google-play-scraper Python package to pull Android app metrics
for tracked entities.

Metrics produced:
    playstore_rating              — average user rating
    playstore_rating_count        — total number of ratings
    playstore_last_update_days_ago — days since the app was last updated

Note: app version string (e.g. "2.1.0") is stored in the raw MetricSnapshot
payload under the key "version" but is not emitted as a flat Metric because
it is not numeric.
"""

import logging
from datetime import datetime, timezone
from typing import Any

from core.models import Entity
from fetchers.base import BaseFetcher
from fetchers.utils import parse_date, to_float

logger = logging.getLogger(__name__)


class PlayStoreFetcher(BaseFetcher):
    """Fetches Google Play Store metrics via google-play-scraper."""

    source_name = "playstore"

    def __init__(self, entity: Entity) -> None:
        super().__init__(entity)

    # ------------------------------------------------------------------
    # fetch
    # ------------------------------------------------------------------

    def fetch(self) -> dict[str, Any]:
        """
        Query the Google Play Store by package name.
        Returns an empty dict if the entity has no play_store_id configured.
        """
        if not self.entity.play_store_id:
            logger.debug("Entity '%s' has no play_store_id — skipping.", self.entity.name)
            return {}

        from google_play_scraper import app as gplay_app
        from google_play_scraper.exceptions import NotFoundError

        try:
            result = gplay_app(self.entity.play_store_id, lang="en", country="us")
        except NotFoundError:
            logger.warning(
                "Play Store app not found for '%s' (id=%s).",
                self.entity.name,
                self.entity.play_store_id,
            )
            return {}
        except Exception as exc:
            logger.warning(
                "Play Store request failed for '%s' (id=%s): %s",
                self.entity.name,
                self.entity.play_store_id,
                exc,
            )
            return {}

        return {
            "app_name": result.get("title", ""),
            "rating": result.get("score"),
            "rating_count": result.get("ratings"),
            # Version string stored in raw snapshot only (not a numeric metric)
            "version": result.get("version", ""),
            "last_updated": result.get("lastUpdatedOn", ""),
        }

    # ------------------------------------------------------------------
    # extract_metrics
    # ------------------------------------------------------------------

    def extract_metrics(self, data: dict[str, Any]) -> dict[str, float | None]:
        """Map Play Store payload to flat metric key/value pairs."""
        metrics: dict[str, float | None] = {}

        if data.get("rating") is not None:
            metrics["playstore_rating"] = to_float(data["rating"])
        if data.get("rating_count") is not None:
            metrics["playstore_rating_count"] = to_float(data["rating_count"])

        # Days since the app was last updated (enables staleness delta detection)
        if data.get("last_updated"):
            published = parse_date(data["last_updated"])
            if published:
                delta = datetime.now(tz=timezone.utc) - published
                metrics["playstore_last_update_days_ago"] = float(delta.days)

        return metrics

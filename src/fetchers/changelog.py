# fetchers/changelog.py
"""
Changelog fetcher.

Parses the GitHub releases.atom RSS feed for each entity with
github_org + github_repo configured. Provides independent release
tracking separate from the GitHub API fetcher.

Metrics produced:
    changelog_releases_total         — total number of releases in the feed
    changelog_latest_release_days_ago — days since the most recent release
"""

import logging
from datetime import datetime, timezone
from typing import Any

import feedparser

from core.models import Entity
from fetchers.base import BaseFetcher
from fetchers.utils import parse_date

logger = logging.getLogger(__name__)

RELEASES_ATOM_URL = "https://github.com/{org}/{repo}/releases.atom"


class ChangelogFetcher(BaseFetcher):
    """Fetches release changelog from GitHub releases.atom RSS feed."""

    source_name = "changelog"

    def __init__(self, entity: Entity) -> None:
        super().__init__(entity)

    # ------------------------------------------------------------------
    # fetch
    # ------------------------------------------------------------------

    def fetch(self) -> dict[str, Any]:
        """
        Parse the releases.atom feed for this entity's GitHub repo.
        Returns an empty dict if the entity has no github_org or github_repo.
        """
        if not self.entity.github_org or not self.entity.github_repo:
            logger.debug(
                "Entity '%s' has no github_org/repo — skipping changelog.",
                self.entity.name,
            )
            return {}

        url = RELEASES_ATOM_URL.format(org=self.entity.github_org, repo=self.entity.github_repo)
        feed = feedparser.parse(url)

        if feed.bozo and not feed.entries:
            # feedparser sets bozo=True on parse errors; only warn if no entries
            logger.warning(
                "Atom feed parse error for '%s' (%s): %s",
                self.entity.name,
                url,
                feed.bozo_exception,
            )
            return {}

        if not feed.entries:
            logger.debug("No releases found in atom feed for '%s'.", self.entity.name)
            return {}

        # Build list of release entries (feed is reverse-chronological)
        entries = []
        for entry in feed.entries:
            # The Atom <id> for GitHub releases is the full URL ending in /tag/<tag>
            tag = ""
            entry_id = entry.get("id", "")
            if entry_id:
                tag = entry_id.rsplit("/", 1)[-1]

            entries.append(
                {
                    "title": entry.get("title", ""),
                    "tag": tag,
                    "published": entry.get("updated", ""),
                    "url": entry.get("link", "")
                    or (entry.links[0].get("href", "") if getattr(entry, "links", None) else ""),
                }
            )

        latest = entries[0]

        return {
            "releases_total": len(entries),
            "latest_tag": latest["tag"],
            "latest_title": latest["title"],
            "latest_published": latest["published"],
            "latest_url": latest["url"],
            "entries": entries[:10],  # store only the 10 most recent to limit payload size
        }

    # ------------------------------------------------------------------
    # extract_metrics
    # ------------------------------------------------------------------

    def extract_metrics(self, data: dict[str, Any]) -> dict[str, float | None]:
        """Map changelog payload to flat metric key/value pairs."""
        metrics: dict[str, float | None] = {}

        if "releases_total" in data:
            metrics["changelog_releases_total"] = float(data["releases_total"])

        # Days since the most recent release
        if data.get("latest_published"):
            published = parse_date(data["latest_published"])
            if published:
                delta = datetime.now(tz=timezone.utc) - published
                metrics["changelog_latest_release_days_ago"] = float(delta.days)

        return metrics

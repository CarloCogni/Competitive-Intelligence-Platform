# fetchers/news.py
"""
News fetcher.

Pulls articles from Google News RSS for each entity's configured keywords.
Deduplicates by URL. Tags articles to the triggering entity via M2M.

Metrics produced:
    news_article_count — total new articles found in this fetch cycle
"""

import logging
from typing import Any
from urllib.parse import quote_plus

import feedparser
import requests

from core.models import Entity
from fetchers.base import BaseFetcher
from fetchers.utils import parse_date
from metrics.models import NewsItem

logger = logging.getLogger(__name__)

# Google News RSS endpoint — no API key required
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"

# Max keywords to query per entity per run (avoid hammering Google RSS)
MAX_KEYWORDS = 3

# Timeout for RSS feed HTTP requests (seconds)
_RSS_TIMEOUT = 10


class NewsFetcher(BaseFetcher):
    """Fetches news articles from Google News RSS for a tracked entity."""

    source_name = "news"

    def __init__(self, entity: Entity) -> None:
        super().__init__(entity)

    # ------------------------------------------------------------------
    # fetch
    # ------------------------------------------------------------------

    def fetch(self) -> dict[str, Any]:
        """
        Search Google News RSS for each configured keyword.
        Returns a payload dict with a list of raw article entries.
        Returns an empty dict if the entity has no news_keywords.
        """
        keywords = self.entity.news_keywords
        if not keywords:
            logger.debug("Entity '%s' has no news_keywords — skipping.", self.entity.name)
            return {}

        articles: list[dict[str, Any]] = []
        seen_urls: set[str] = set()

        # Only query the first MAX_KEYWORDS to stay within Google's rate limits
        for keyword in keywords[:MAX_KEYWORDS]:
            url = GOOGLE_NEWS_RSS.format(query=quote_plus(keyword))
            # Fetch with an explicit timeout to avoid blocking indefinitely,
            # then parse the already-downloaded content.
            try:
                resp = requests.get(url, timeout=_RSS_TIMEOUT)
                resp.raise_for_status()
            except requests.RequestException:
                logger.warning("RSS fetch failed for keyword '%s'.", keyword)
                continue
            feed = feedparser.parse(resp.content)

            for entry in feed.entries:
                link = entry.get("link", "")
                if not link or link in seen_urls:
                    continue

                title = entry.get("title", "")

                # Relevance gate: skip articles whose title contains none of the
                # entity's keywords. Google News RSS sometimes returns
                # geographically or contextually related results that are
                # entirely off-topic (e.g. "Phoenix wallet" can surface
                # Phoenix, AZ city news). The title is the most reliable signal
                # available in the feed without fetching article content.
                title_lower = title.lower()
                if not any(kw.lower() in title_lower for kw in keywords):
                    logger.debug(
                        "Skipping irrelevant article for '%s': %r",
                        self.entity.name,
                        title,
                    )
                    continue

                seen_urls.add(link)
                articles.append(
                    {
                        "title": title,
                        "url": link,
                        "source": entry.get("source", {}).get("title", ""),
                        "published": entry.get("published", ""),
                    }
                )

        return {"articles": articles}

    # ------------------------------------------------------------------
    # extract_metrics
    # ------------------------------------------------------------------

    def extract_metrics(self, data: dict[str, Any]) -> dict[str, float | None]:
        """Return article count as a trackable metric for delta detection."""
        count = len(data.get("articles", []))
        return {"news_article_count": float(count)}

    # ------------------------------------------------------------------
    # store override — also create/update NewsItem rows
    # ------------------------------------------------------------------

    def store(self, data: dict[str, Any]) -> None:
        """
        Persist snapshot + metrics (via super), then upsert NewsItem rows
        and link them to this entity via M2M.
        """
        super().store(data)
        self._upsert_news_items(data.get("articles", []))

    def _upsert_news_items(self, articles: list[dict[str, Any]]) -> None:
        """Create new NewsItem rows; skip URLs that already exist (dedup)."""
        new_count = 0
        for article in articles:
            url = article.get("url", "")
            if not url:
                continue

            published_at = parse_date(article.get("published"))

            # get_or_create on url (unique constraint = dedup key)
            item, created = NewsItem.objects.get_or_create(
                url=url,
                defaults={
                    "title": article.get("title", "")[:500],
                    "source_name": article.get("source", "")[:200],
                    "published_at": published_at,
                },
            )
            # Always link to this entity (M2M is idempotent)
            item.entities.add(self.entity)

            if created:
                new_count += 1

        logger.info(
            "News: %d new article(s) stored for '%s'.",
            new_count,
            self.entity.name,
        )

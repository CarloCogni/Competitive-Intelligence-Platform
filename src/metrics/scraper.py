# metrics/scraper.py
"""
Article body scraper for competitor detection.

Uses trafilatura for clean text extraction from news article URLs.
Google News redirect URLs are not scrapable (they use encrypted protobuf
encoding), so articles from Google News RSS rely on title-only analysis.
Results are cached on NewsItem.content to avoid re-scraping.
"""

from __future__ import annotations

import logging

from django.utils import timezone

logger = logging.getLogger(__name__)

# Max characters of article content to store (prevents bloating SQLite)
MAX_CONTENT_LENGTH = 5_000

# Max words of content to include in LLM prompt
LLM_CONTENT_WORDS = 500

# HTTP timeout for downloading article pages (seconds)
_SCRAPE_TIMEOUT = 10

# Domains whose redirect URLs can't be resolved without a headless browser
_UNSCRAPPABLE_DOMAINS = ("news.google.com",)


def scrape_article(news_item: object) -> str:
    """
    Scrape and cache article body for a NewsItem.

    Uses trafilatura to download and extract clean text from the article URL.
    Skips URLs from domains known to be unscrappable (e.g. Google News
    redirect URLs). Results are cached on ``news_item.content`` /
    ``news_item.content_scraped_at`` so subsequent calls return the cached
    value without re-fetching.

    Args:
        news_item: A ``metrics.models.NewsItem`` instance.

    Returns:
        The article body text, or empty string if scraping failed,
        the URL is unscrappable, or the article was already scraped.
    """
    # Return cached content if already scraped
    if news_item.content_scraped_at is not None:
        return news_item.content or ""

    body = ""
    url = news_item.url or ""

    # Skip domains that use JavaScript redirects / encrypted URLs
    is_scrappable = url and not any(domain in url for domain in _UNSCRAPPABLE_DOMAINS)

    if is_scrappable:
        try:
            import trafilatura

            downloaded = trafilatura.fetch_url(url)
            if downloaded:
                extracted = trafilatura.extract(downloaded)
                if extracted:
                    body = extracted[:MAX_CONTENT_LENGTH]
        except Exception as exc:
            logger.debug("Scrape failed for %s: %s", url, exc)

    # Cache result (even on failure — prevents retrying dead URLs)
    news_item.content = body if body else ""
    news_item.content_scraped_at = timezone.now()
    news_item.save(update_fields=["content", "content_scraped_at"])

    if body:
        logger.debug("Scraped %d chars from %s", len(body), url)
    return body


def truncate_to_words(text: str, max_words: int = LLM_CONTENT_WORDS) -> str:
    """Return the first *max_words* words of *text*."""
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words])

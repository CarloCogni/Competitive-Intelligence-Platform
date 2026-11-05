# metrics/tests/test_scraper.py
"""Tests for metrics.scraper — article body scraping and text truncation."""

from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone as django_tz

from metrics.scraper import truncate_to_words


class TestTruncateToWords:
    """Tests for truncate_to_words — word-limit truncation."""

    def test_short_text_unchanged(self):
        assert truncate_to_words("hello world", max_words=10) == "hello world"

    def test_long_text_truncated(self):
        text = " ".join(f"word{i}" for i in range(20))
        result = truncate_to_words(text, max_words=5)
        assert result == "word0 word1 word2 word3 word4"

    def test_empty_string(self):
        assert truncate_to_words("") == ""

    def test_exact_boundary(self):
        text = "one two three"
        assert truncate_to_words(text, max_words=3) == "one two three"


@pytest.mark.django_db
class TestScrapeArticle:
    """Tests for scrape_article — download and cache article body."""

    def _make_news_item(self, url="https://example.com/article", scraped_at=None, content=""):
        """Create a mock NewsItem-like object with save() tracking."""
        item = MagicMock()
        item.url = url
        item.content_scraped_at = scraped_at
        item.content = content
        return item

    def test_already_scraped_returns_cached(self):
        from metrics.scraper import scrape_article

        item = self._make_news_item(scraped_at=django_tz.now(), content="cached body")
        result = scrape_article(item)
        assert result == "cached body"
        item.save.assert_not_called()

    def test_unscrappable_domain_caches_empty(self):
        from metrics.scraper import scrape_article

        item = self._make_news_item(url="https://news.google.com/redirect/abc")
        result = scrape_article(item)
        assert result == ""
        item.save.assert_called_once()

    @patch.dict("sys.modules", {"trafilatura": MagicMock()})
    def test_successful_scrape(self):
        import sys

        from metrics.scraper import scrape_article

        mock_traf = sys.modules["trafilatura"]
        mock_traf.fetch_url.return_value = "<html>content</html>"
        mock_traf.extract.return_value = "Extracted article body text"
        item = self._make_news_item()
        result = scrape_article(item)
        assert result == "Extracted article body text"
        assert item.content == "Extracted article body text"
        item.save.assert_called_once()

    @patch.dict("sys.modules", {"trafilatura": MagicMock()})
    def test_scrape_failure_caches_empty(self):
        import sys

        from metrics.scraper import scrape_article

        mock_traf = sys.modules["trafilatura"]
        mock_traf.fetch_url.side_effect = Exception("Network error")
        item = self._make_news_item()
        result = scrape_article(item)
        assert result == ""
        item.save.assert_called_once()

# metrics/tests/test_news_enrichment.py
"""Tests for the LLM-powered news enrichment pipeline."""

import json
from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings
from django.utils import timezone

from metrics.news_enrichment import (
    _build_article_batches,
    _bullets_to_html,
    _estimate_tokens,
    _llm_classify_batch,
    _strip_markdown_fences,
    generate_key_takeaways,
    run_news_enrichment,
)

# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestEstimateTokens:
    def test_empty_string_returns_zero(self):
        assert _estimate_tokens("") == 0

    def test_known_length_string(self):
        # 100 chars / 4 chars_per_token = 25
        assert _estimate_tokens("a" * 100) == 25


class TestBuildArticleBatches:
    def test_single_article_fits_in_one_batch(self):
        articles = [(1, "Title", "Short content", "https://example.com")]
        batches = _build_article_batches(articles, context_budget=10_000)
        assert len(batches) == 1
        assert len(batches[0]) == 1

    def test_splits_when_budget_exceeded(self):
        # Each article ~1000 tokens (4000 chars), budget = 6000 tokens
        big_content = "x" * 4000
        articles = [(i, "Title", big_content, f"https://example.com/{i}") for i in range(5)]
        batches = _build_article_batches(articles, context_budget=6_000)
        assert len(batches) > 1

    def test_splits_at_max_articles_per_batch(self):
        # 51 tiny articles — first 50 in batch 1, last 1 in batch 2
        articles = [(i, "T", "", f"https://example.com/{i}") for i in range(51)]
        batches = _build_article_batches(articles, context_budget=1_000_000)
        assert len(batches) == 2
        assert len(batches[0]) == 50
        assert len(batches[1]) == 1

    def test_empty_articles_returns_empty(self):
        batches = _build_article_batches([], context_budget=10_000)
        assert batches == []


class TestStripMarkdownFences:
    def test_strips_json_fences(self):
        text = '```json\n[{"key": "value"}]\n```'
        assert _strip_markdown_fences(text) == '[{"key": "value"}]'

    def test_no_fences_unchanged(self):
        text = '[{"key": "value"}]'
        assert _strip_markdown_fences(text) == text

    def test_handles_no_newline_after_backticks(self):
        text = "```"
        result = _strip_markdown_fences(text)
        assert isinstance(result, str)


class TestBulletsToHtml:
    def test_converts_dash_bullets(self):
        text = "- First\n- Second"
        html = _bullets_to_html(text)
        assert "<li>First</li>" in html
        assert "<li>Second</li>" in html
        assert html.startswith("<ul")

    def test_converts_numbered_bullets(self):
        text = "1. First\n2. Second"
        html = _bullets_to_html(text)
        assert "<li>First</li>" in html
        assert "<li>Second</li>" in html

    def test_empty_input_wraps_in_paragraph(self):
        html = _bullets_to_html("")
        assert "<p" in html

    def test_mixed_prefixes(self):
        text = "- Dash\n* Star\n1. Numbered"
        html = _bullets_to_html(text)
        assert "<li>Dash</li>" in html
        assert "<li>Star</li>" in html
        assert "<li>Numbered</li>" in html


# ---------------------------------------------------------------------------
# LLM classify batch (mocked client)
# ---------------------------------------------------------------------------


class TestLlmClassifyBatch:
    def _make_batch(self, n=2):
        return [(i, f"Title {i}", f"Content {i}", f"https://example.com/{i}") for i in range(n)]

    def test_happy_path_returns_classifications(self):
        client = MagicMock()
        client.chat.return_value = json.dumps(
            [
                {
                    "article_index": 0,
                    "sentiment": "positive",
                    "signal_type": "product_launch",
                    "summary": "Good news",
                    "key_quote": "Quote here",
                },
                {
                    "article_index": 1,
                    "sentiment": "negative",
                    "signal_type": "vulnerability",
                    "summary": "Bad news",
                    "key_quote": "Another quote",
                },
            ]
        )
        batch = self._make_batch(2)
        result = _llm_classify_batch(client, batch)
        assert len(result) == 2
        assert result[0]["sentiment"] == "positive"
        assert result[1]["signal_type"] == "vulnerability"

    def test_invalid_json_returns_empty(self):
        client = MagicMock()
        client.chat.return_value = "not valid json at all"
        result = _llm_classify_batch(client, self._make_batch())
        assert result == []

    def test_llm_exception_returns_empty(self):
        client = MagicMock()
        client.chat.side_effect = RuntimeError("LLM down")
        result = _llm_classify_batch(client, self._make_batch())
        assert result == []

    def test_invalid_sentiment_normalized_to_neutral(self):
        client = MagicMock()
        client.chat.return_value = json.dumps(
            [
                {
                    "article_index": 0,
                    "sentiment": "INVALID_VALUE",
                    "signal_type": "other",
                    "summary": "test",
                    "key_quote": "q",
                },
            ]
        )
        result = _llm_classify_batch(client, self._make_batch(1))
        assert len(result) == 1
        assert result[0]["sentiment"] == "neutral"

    def test_out_of_range_article_index_skipped(self):
        client = MagicMock()
        client.chat.return_value = json.dumps(
            [
                {
                    "article_index": 999,
                    "sentiment": "positive",
                    "signal_type": "other",
                    "summary": "s",
                    "key_quote": "q",
                },
            ]
        )
        result = _llm_classify_batch(client, self._make_batch(1))
        assert result == []


# ---------------------------------------------------------------------------
# Integration: run_news_enrichment (all I/O mocked)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRunNewsEnrichment:
    @override_settings(NEWS_ENRICHMENT_OLLAMA_ONLY=True)
    @patch("metrics.llm_review.LLMClient.from_settings")
    def test_returns_zero_when_ollama_unavailable(self, mock_from_settings):
        mock_client = MagicMock()
        mock_client.is_ollama_available.return_value = False
        mock_from_settings.return_value = mock_client
        assert run_news_enrichment() == 0

    @override_settings(NEWS_ENRICHMENT_OLLAMA_ONLY=True)
    @patch("metrics.llm_review.LLMClient.from_settings")
    @patch("metrics.scraper.scrape_article", return_value="Article body text")
    @patch("metrics.scraper.truncate_to_words", side_effect=lambda x: x)
    def test_enriches_articles_and_returns_count(
        self, mock_truncate, mock_scrape, mock_from_settings
    ):
        from metrics.tests.factories import NewsItemFactory

        mock_client = MagicMock()
        mock_client.is_ollama_available.return_value = True
        mock_client.chat.return_value = json.dumps(
            [
                {
                    "article_index": 0,
                    "sentiment": "positive",
                    "signal_type": "product_launch",
                    "summary": "Good news",
                    "key_quote": "Quote",
                },
            ]
        )
        mock_from_settings.return_value = mock_client

        item = NewsItemFactory(enriched_at=None, fetched_at=timezone.now())
        result = run_news_enrichment()
        assert result == 1
        item.refresh_from_db()
        assert item.sentiment == "positive"
        assert item.enriched_at is not None

    @override_settings(NEWS_ENRICHMENT_OLLAMA_ONLY=True)
    @patch("metrics.llm_review.LLMClient.from_settings")
    def test_no_unenriched_articles_returns_zero(self, mock_from_settings):
        mock_client = MagicMock()
        mock_client.is_ollama_available.return_value = True
        mock_from_settings.return_value = mock_client
        assert run_news_enrichment() == 0


# ---------------------------------------------------------------------------
# Integration: generate_key_takeaways
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGenerateKeyTakeaways:
    @patch("metrics.llm_review.LLMClient.from_settings")
    def test_returns_html_bullets(self, mock_from_settings):
        from metrics.tests.factories import NewsItemFactory

        mock_client = MagicMock()
        mock_client.chat.return_value = "- Key insight one\n- Key insight two"
        mock_from_settings.return_value = mock_client

        NewsItemFactory(
            enriched_at=timezone.now(),
            published_at=timezone.now(),
            llm_summary="Test summary",
            sentiment="positive",
        )
        result = generate_key_takeaways(days=7)
        assert "<li>" in result
        assert "Key insight" in result

    def test_no_enriched_articles_returns_message(self):
        result = generate_key_takeaways(days=7)
        assert "No enriched articles" in result

    @patch("metrics.llm_review.LLMClient.from_settings")
    def test_llm_failure_returns_error_message(self, mock_from_settings):
        from metrics.tests.factories import NewsItemFactory

        mock_client = MagicMock()
        mock_client.chat.side_effect = RuntimeError("LLM down")
        mock_from_settings.return_value = mock_client

        NewsItemFactory(
            enriched_at=timezone.now(),
            published_at=timezone.now(),
            llm_summary="Test summary",
            sentiment="positive",
        )
        result = generate_key_takeaways(days=7)
        assert "Failed" in result or "unavailable" in result.lower()

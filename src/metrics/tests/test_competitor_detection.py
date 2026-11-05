# metrics/tests/test_competitor_detection.py
"""Tests for competitor detection in delta.py (run_competitor_detection)."""

import json
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from core.tests.factories import EntityFactory
from metrics.delta import (
    _build_article_batches as delta_build_batches,
)
from metrics.delta import (
    _estimate_tokens as delta_estimate_tokens,
)
from metrics.delta import (
    _llm_extract_competitors,
    run_competitor_detection,
)
from metrics.models import CandidateEntity
from metrics.tests.factories import NewsItemFactory


class TestDeltaEstimateTokens:
    def test_known_length(self):
        assert delta_estimate_tokens("a" * 400) == 100


class TestDeltaBuildArticleBatches:
    def test_single_batch(self):
        articles = [(1, "Title", "Body", "https://example.com")]
        batches = delta_build_batches(articles, context_budget=10_000)
        assert len(batches) == 1

    def test_multiple_batches(self):
        big = "x" * 4000
        articles = [(i, "T", big, f"https://example.com/{i}") for i in range(5)]
        batches = delta_build_batches(articles, context_budget=6_000)
        assert len(batches) > 1


class TestLlmExtractCompetitors:
    def test_happy_path(self):
        client = MagicMock()
        client.chat.return_value = json.dumps(
            [
                {
                    "article_index": 0,
                    "entities": [
                        {
                            "name": "NewCorp",
                            "category": "competitor",
                            "relevance": 8,
                            "reason": "Direct competitor",
                        },
                    ],
                },
            ]
        )
        batch = [(1, "Title", "Content about NewCorp", "https://example.com/1")]
        tracked = {"Blockstream"}
        result = _llm_extract_competitors(client, batch, tracked)
        assert len(result) == 1
        assert result[0]["name"] == "NewCorp"

    def test_filters_tracked_entities(self):
        client = MagicMock()
        client.chat.return_value = json.dumps(
            [
                {
                    "article_index": 0,
                    "entities": [
                        {
                            "name": "Blockstream",
                            "category": "competitor",
                            "relevance": 9,
                            "reason": "Self",
                        },
                    ],
                },
            ]
        )
        batch = [(1, "Title", "Content", "https://example.com/1")]
        tracked = {"Blockstream"}
        result = _llm_extract_competitors(client, batch, tracked)
        assert result == []

    def test_llm_failure_returns_empty(self):
        client = MagicMock()
        client.chat.side_effect = RuntimeError("LLM down")
        batch = [(1, "Title", "Content", "https://example.com/1")]
        result = _llm_extract_competitors(client, batch, {"Blockstream"})
        assert result == []

    def test_invalid_json_returns_empty(self):
        client = MagicMock()
        client.chat.return_value = "not json"
        batch = [(1, "Title", "Content", "https://example.com/1")]
        result = _llm_extract_competitors(client, batch, {"Blockstream"})
        assert result == []


@pytest.mark.django_db
class TestRunCompetitorDetection:
    @patch("metrics.llm_review.LLMClient.from_settings")
    @patch("metrics.scraper.scrape_article", return_value="Article body")
    @patch("metrics.scraper.truncate_to_words", side_effect=lambda x: x)
    def test_creates_candidate_entities(self, mock_trunc, mock_scrape, mock_from_settings):
        # Setup: primary entity + recent news
        EntityFactory(slug="blockstream", is_primary=True)
        NewsItemFactory(fetched_at=timezone.now())

        mock_client = MagicMock()
        mock_client.chat.return_value = json.dumps(
            [
                {
                    "article_index": 0,
                    "entities": [
                        {
                            "name": "RivalCorp",
                            "category": "competitor",
                            "relevance": 8,
                            "reason": "Competitor",
                        },
                    ],
                },
            ]
        )
        mock_from_settings.return_value = mock_client

        run_competitor_detection()
        assert CandidateEntity.objects.filter(name="RivalCorp").exists()

    @patch("metrics.llm_review.LLMClient.from_settings")
    def test_no_articles_returns_zero(self, mock_from_settings):
        mock_from_settings.return_value = MagicMock()
        result = run_competitor_detection()
        assert result == 0

    @patch("metrics.llm_review.LLMClient.from_settings")
    def test_llm_unavailable_returns_zero(self, mock_from_settings):
        mock_from_settings.side_effect = RuntimeError("No LLM")
        result = run_competitor_detection()
        assert result == 0

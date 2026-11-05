# metrics/tests/test_llm_review.py
"""
Unit tests for metrics/llm_review.py.

All HTTP calls are mocked via the responses library — no live Ollama or
Groq service is required.
"""

import json

import pytest
import responses as resp

from metrics.llm_review import (
    AnomalyLLMReviewer,
    LLMClient,
    ThresholdSuggestion,
)

OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"
OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"
GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ollama_client():
    """LLMClient configured for Ollama only (no Groq key)."""
    return LLMClient(
        ollama_base_url="http://localhost:11434",
        ollama_model="test-model",
        groq_api_key="",
        groq_model="",
    )


@pytest.fixture
def dual_client():
    """LLMClient configured with both Ollama and Groq."""
    return LLMClient(
        ollama_base_url="http://localhost:11434",
        ollama_model="test-model",
        groq_api_key="gsk_test_key",
        groq_model="llama-3.1-8b-instant",
    )


def _ollama_chat_response(content: str) -> dict:
    """Build a mock Ollama /api/chat response body."""
    return {"message": {"role": "assistant", "content": content}}


def _groq_chat_response(content: str) -> dict:
    """Build a mock Groq /v1/chat/completions response body."""
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


# ---------------------------------------------------------------------------
# TestLLMClient
# ---------------------------------------------------------------------------


class TestLLMClient:
    """Tests for LLMClient Ollama/Groq routing."""

    @resp.activate
    def test_chat_calls_ollama_when_available(self, ollama_client):
        """chat() uses Ollama when the service is reachable."""
        resp.add(resp.GET, OLLAMA_TAGS_URL, json={"models": []}, status=200)
        resp.add(
            resp.POST,
            OLLAMA_CHAT_URL,
            json=_ollama_chat_response("hello from ollama"),
            status=200,
        )
        result = ollama_client.chat(system="sys", user="usr")
        assert "hello from ollama" in result

    @resp.activate
    def test_falls_back_to_groq_when_ollama_unavailable(self, dual_client):
        """chat() falls back to Groq when Ollama returns 503."""
        resp.add(resp.GET, OLLAMA_TAGS_URL, status=503)
        resp.add(
            resp.POST,
            GROQ_CHAT_URL,
            json=_groq_chat_response("hello from groq"),
            status=200,
        )
        result = dual_client.chat(system="sys", user="usr")
        assert "hello from groq" in result

    @resp.activate
    def test_raises_when_both_unavailable(self, ollama_client):
        """chat() raises RuntimeError when Ollama is down and no Groq key."""
        resp.add(resp.GET, OLLAMA_TAGS_URL, status=503)
        with pytest.raises(RuntimeError, match="No LLM backend available"):
            ollama_client.chat(system="sys", user="usr")

    @resp.activate
    def test_groq_sends_auth_header(self, dual_client):
        """Groq requests include the Authorization Bearer header."""
        resp.add(resp.GET, OLLAMA_TAGS_URL, status=503)
        resp.add(
            resp.POST,
            GROQ_CHAT_URL,
            json=_groq_chat_response("ok"),
            status=200,
        )
        dual_client.chat(system="sys", user="usr")
        assert resp.calls[1].request.headers["Authorization"] == "Bearer gsk_test_key"


# ---------------------------------------------------------------------------
# TestAnomalyLLMReviewer
# ---------------------------------------------------------------------------


class TestAnomalyLLMReviewer:
    """Tests for AnomalyLLMReviewer with mocked LLM responses."""

    @resp.activate
    def test_review_proceed_verdict(self, ollama_client):
        """LLM returns proceed verdict with custom contamination."""
        resp.add(resp.GET, OLLAMA_TAGS_URL, json={"models": []}, status=200)
        resp.add(
            resp.POST,
            OLLAMA_CHAT_URL,
            json=_ollama_chat_response(
                json.dumps(
                    {
                        "verdict": "proceed",
                        "reason": "distribution looks normal",
                        "recommended_contamination": 0.03,
                    }
                )
            ),
            status=200,
        )
        reviewer = AnomalyLLMReviewer(client=ollama_client)
        result = reviewer.review_training_data(
            entity_slug="blockstream",
            metric_key="github_stars",
            n=50,
            mean_change=0.01,
            std_change=0.02,
            p5=-0.03,
            p95=0.05,
            span_days=120,
            pct_monotonic=0.6,
        )
        assert result.verdict == "proceed"
        assert result.recommended_contamination == 0.03

    @resp.activate
    def test_review_skip_verdict(self, ollama_client):
        """LLM returns skip verdict — blocks training."""
        resp.add(resp.GET, OLLAMA_TAGS_URL, json={"models": []}, status=200)
        resp.add(
            resp.POST,
            OLLAMA_CHAT_URL,
            json=_ollama_chat_response(
                json.dumps(
                    {
                        "verdict": "skip",
                        "reason": "strongly monotonic, 95% positive",
                        "recommended_contamination": 0.05,
                    }
                )
            ),
            status=200,
        )
        reviewer = AnomalyLLMReviewer(client=ollama_client)
        result = reviewer.review_training_data(
            entity_slug="test",
            metric_key="github_stars",
            n=40,
            mean_change=0.05,
            std_change=0.01,
            p5=0.02,
            p95=0.08,
            span_days=90,
            pct_monotonic=0.95,
        )
        assert result.verdict == "skip"

    @resp.activate
    def test_review_invalid_json_returns_proceed_default(self, ollama_client):
        """Malformed LLM response falls back to proceed with default contamination."""
        resp.add(resp.GET, OLLAMA_TAGS_URL, json={"models": []}, status=200)
        resp.add(
            resp.POST,
            OLLAMA_CHAT_URL,
            json=_ollama_chat_response("This is not JSON at all."),
            status=200,
        )
        reviewer = AnomalyLLMReviewer(client=ollama_client)
        result = reviewer.review_training_data(
            entity_slug="test",
            metric_key="github_stars",
            n=40,
            mean_change=0.01,
            std_change=0.02,
            p5=-0.03,
            p95=0.05,
            span_days=90,
            pct_monotonic=0.6,
        )
        assert result.verdict == "proceed"
        assert result.reason == "parse_error"
        assert result.recommended_contamination == 0.05

    @resp.activate
    def test_review_llm_unavailable_returns_proceed(self):
        """Both backends down — review returns safe default (proceed)."""
        resp.add(resp.GET, OLLAMA_TAGS_URL, status=503)
        client = LLMClient(
            ollama_base_url="http://localhost:11434",
            ollama_model="test",
            groq_api_key="",
            groq_model="",
        )
        reviewer = AnomalyLLMReviewer(client=client)
        result = reviewer.review_training_data(
            entity_slug="test",
            metric_key="github_stars",
            n=40,
            mean_change=0.01,
            std_change=0.02,
            p5=-0.03,
            p95=0.05,
            span_days=90,
            pct_monotonic=0.6,
        )
        assert result.verdict == "proceed"
        assert result.reason == "llm_unavailable"

    @resp.activate
    def test_suggest_cold_start_returns_threshold(self, ollama_client):
        """LLM suggests a valid cold-start threshold."""
        resp.add(resp.GET, OLLAMA_TAGS_URL, json={"models": []}, status=200)
        resp.add(
            resp.POST,
            OLLAMA_CHAT_URL,
            json=_ollama_chat_response(
                json.dumps({"threshold_pct": 0.25, "reason": "small entity, volatile vertical"})
            ),
            status=200,
        )
        reviewer = AnomalyLLMReviewer(client=ollama_client)
        result = reviewer.suggest_cold_start_threshold(
            entity_slug="jade",
            metric_key="github_stars",
            vertical="Wallet (HW)",
            n_available=10,
            min_points=30,
        )
        assert isinstance(result, ThresholdSuggestion)
        assert result.threshold_pct == 0.25

    @resp.activate
    def test_suggest_cold_start_invalid_json_returns_none(self, ollama_client):
        """Malformed cold-start response returns None."""
        resp.add(resp.GET, OLLAMA_TAGS_URL, json={"models": []}, status=200)
        resp.add(
            resp.POST,
            OLLAMA_CHAT_URL,
            json=_ollama_chat_response("I cannot help with that."),
            status=200,
        )
        reviewer = AnomalyLLMReviewer(client=ollama_client)
        result = reviewer.suggest_cold_start_threshold(
            entity_slug="jade",
            metric_key="github_stars",
            vertical="Wallet (HW)",
            n_available=10,
            min_points=30,
        )
        assert result is None

    @resp.activate
    def test_suggest_cold_start_invalid_threshold_returns_none(self, ollama_client):
        """Threshold outside valid range (0, 1.0] returns None."""
        resp.add(resp.GET, OLLAMA_TAGS_URL, json={"models": []}, status=200)
        resp.add(
            resp.POST,
            OLLAMA_CHAT_URL,
            json=_ollama_chat_response(json.dumps({"threshold_pct": 5.0, "reason": "confused"})),
            status=200,
        )
        reviewer = AnomalyLLMReviewer(client=ollama_client)
        result = reviewer.suggest_cold_start_threshold(
            entity_slug="jade",
            metric_key="github_stars",
            vertical="Wallet (HW)",
            n_available=10,
            min_points=30,
        )
        assert result is None

    @resp.activate
    def test_review_markdown_fenced_json(self, ollama_client):
        """LLM response wrapped in ```json fences is parsed correctly."""
        resp.add(resp.GET, OLLAMA_TAGS_URL, json={"models": []}, status=200)
        fenced = '```json\n{"verdict": "warn", "reason": "slightly flat", "recommended_contamination": 0.07}\n```'
        resp.add(
            resp.POST,
            OLLAMA_CHAT_URL,
            json=_ollama_chat_response(fenced),
            status=200,
        )
        reviewer = AnomalyLLMReviewer(client=ollama_client)
        result = reviewer.review_training_data(
            entity_slug="test",
            metric_key="github_stars",
            n=40,
            mean_change=0.001,
            std_change=0.001,
            p5=-0.001,
            p95=0.003,
            span_days=90,
            pct_monotonic=0.55,
        )
        assert result.verdict == "warn"
        assert result.recommended_contamination == 0.07

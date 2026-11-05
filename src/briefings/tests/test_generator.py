# briefings/tests/test_generator.py
"""
Tests for OllamaClient and BriefingGenerator.

All HTTP calls to Ollama are mocked via the responses library so no
live Ollama service is required to run these tests.
"""

import pytest
import requests
import responses as resp

from briefings.generator import BriefingGenerator, OllamaClient
from briefings.models import WeeklyBriefing
from briefings.tests.factories import WeeklyBriefingFactory
from core.tests.factories import EntityFactory
from metrics.tests.factories import AlertFactory, MetricFactory, NewsItemFactory

OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"
OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"

MOCK_RESPONSE_BODY = {
    "message": {
        "role": "assistant",
        "content": (
            "**Executive Summary**\n"
            "The competitive landscape shows moderate activity.\n\n"
            "**Key Threats**\n"
            "- Lightning Labs: significant GitHub growth.\n\n"
            "**Opportunities**\n"
            "- Casa App Store rating declined slightly.\n\n"
            "**Watch List**\n"
            "- Galoy: new release detected."
        ),
    }
}


# ---------------------------------------------------------------------------
# OllamaClient
# ---------------------------------------------------------------------------


class TestOllamaClient:
    """Unit tests for the thin HTTP wrapper."""

    @resp.activate
    def test_chat_returns_content(self):
        """chat() returns the model's message content string."""
        resp.add(resp.POST, OLLAMA_CHAT_URL, json=MOCK_RESPONSE_BODY, status=200)
        client = OllamaClient(base_url="http://localhost:11434", model="llama3.1:8b")
        result = client.chat(system="You are an analyst.", user="Summarise this.")
        assert "Executive Summary" in result

    @resp.activate
    def test_chat_sends_correct_payload(self):
        """chat() posts the expected JSON structure to /api/chat."""
        import json

        resp.add(resp.POST, OLLAMA_CHAT_URL, json=MOCK_RESPONSE_BODY, status=200)
        client = OllamaClient(base_url="http://localhost:11434", model="testmodel")
        client.chat(system="sys", user="usr")

        assert len(resp.calls) == 1
        payload = json.loads(resp.calls[0].request.body)
        assert payload["model"] == "testmodel"
        assert payload["stream"] is False
        assert payload["messages"][0]["role"] == "system"
        assert payload["messages"][1]["role"] == "user"

    @resp.activate
    def test_chat_raises_on_http_error(self):
        """chat() propagates HTTPError on non-2xx responses."""
        import requests

        resp.add(resp.POST, OLLAMA_CHAT_URL, status=500)
        client = OllamaClient(base_url="http://localhost:11434", model="llama3.1:8b")
        with pytest.raises(requests.HTTPError):
            client.chat(system="s", user="u")

    @resp.activate
    def test_is_available_true(self):
        """is_available() returns True when Ollama responds 200."""
        resp.add(resp.GET, OLLAMA_TAGS_URL, json={"models": []}, status=200)
        client = OllamaClient(base_url="http://localhost:11434", model="llama3.1:8b")
        assert client.is_available() is True

    @resp.activate
    def test_is_available_false_on_error(self):
        """is_available() returns False (never raises) on connection error."""
        import requests as req_lib

        resp.add(
            resp.GET,
            OLLAMA_TAGS_URL,
            body=req_lib.exceptions.ConnectionError("connection refused"),
        )
        client = OllamaClient(base_url="http://localhost:11434", model="llama3.1:8b")
        assert client.is_available() is False


# ---------------------------------------------------------------------------
# BriefingGenerator
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBriefingGenerator:
    """Integration tests for BriefingGenerator against a real test DB."""

    def _make_client(self) -> OllamaClient:
        """Return an OllamaClient pre-wired to use the mock URL."""
        return OllamaClient(base_url="http://localhost:11434", model="llama3.1:8b")

    @resp.activate
    def test_run_creates_ready_briefing(self, db):
        """run() persists a WeeklyBriefing with status=ready on success."""
        resp.add(resp.POST, OLLAMA_CHAT_URL, json=MOCK_RESPONSE_BODY, status=200)
        briefing = BriefingGenerator(client=self._make_client()).run()

        assert briefing.pk is not None
        assert briefing.status == WeeklyBriefing.Status.READY
        assert "Executive Summary" in briefing.content
        assert briefing.generated_at is not None
        assert briefing.error_message == ""

    @resp.activate
    def test_run_sets_failed_on_http_error(self, db):
        """run() sets status=failed and stores error_message when Ollama returns 500."""
        resp.add(resp.POST, OLLAMA_CHAT_URL, status=500)
        briefing = BriefingGenerator(client=self._make_client()).run()

        assert briefing.status == WeeklyBriefing.Status.FAILED
        assert briefing.error_message != ""
        assert briefing.content == ""

    @resp.activate
    def test_run_counts_alerts_and_entities(self, db):
        """run() records alert_count and entity_count from the prompt context."""
        entity_a = EntityFactory()
        entity_b = EntityFactory()
        AlertFactory(entity=entity_a)
        AlertFactory(entity=entity_a)
        AlertFactory(entity=entity_b)

        resp.add(resp.POST, OLLAMA_CHAT_URL, json=MOCK_RESPONSE_BODY, status=200)
        briefing = BriefingGenerator(client=self._make_client()).run()

        assert briefing.alert_count == 3
        assert briefing.entity_count >= 2

    @resp.activate
    def test_run_counts_news(self, db):
        """run() records news_count from NewsItem records in the period."""
        entity = EntityFactory()
        item = NewsItemFactory()
        item.entities.add(entity)
        NewsItemFactory()  # second item, no entity — still counted

        resp.add(resp.POST, OLLAMA_CHAT_URL, json=MOCK_RESPONSE_BODY, status=200)
        briefing = BriefingGenerator(client=self._make_client()).run()

        assert briefing.news_count == 2

    def test_build_context_includes_alert_sections(self, db):
        """_build_context_block() includes all three severity sections."""
        from django.utils import timezone

        entity = EntityFactory()
        AlertFactory(entity=entity, metadata={"severity": "HIGH"})
        AlertFactory(entity=entity, metadata={"severity": "LOW"})

        gen = BriefingGenerator(client=self._make_client())
        now = timezone.now()
        cutoff = now - timezone.timedelta(days=7)
        data = gen._collect_data(cutoff)
        context = gen._build_context_block(data, cutoff, now)

        assert "HIGH SEVERITY ALERTS" in context
        assert "MEDIUM SEVERITY ALERTS" in context
        assert "LOW SEVERITY ALERTS" in context

    def test_build_context_includes_news(self, db):
        """_build_context_block() includes news headlines section."""
        from django.utils import timezone

        entity = EntityFactory()
        item = NewsItemFactory(title="Test headline for Ollama prompt")
        item.entities.add(entity)

        gen = BriefingGenerator(client=self._make_client())
        now = timezone.now()
        cutoff = now - timezone.timedelta(days=7)
        data = gen._collect_data(cutoff)
        context = gen._build_context_block(data, cutoff, now)

        assert "Test headline for Ollama prompt" in context

    def test_build_context_includes_metric_table(self, db):
        """_build_context_block() includes TOP METRIC CHANGES section."""
        from django.utils import timezone

        entity = EntityFactory()
        cutoff = timezone.now() - timezone.timedelta(days=7)
        # Baseline metric (before cutoff)
        MetricFactory(
            entity=entity,
            key="github_stars",
            value=1000.0,
            captured_at=cutoff - timezone.timedelta(days=1),
        )
        # Recent metric (after cutoff)
        MetricFactory(
            entity=entity,
            key="github_stars",
            value=1300.0,
            captured_at=cutoff + timezone.timedelta(hours=1),
        )

        gen = BriefingGenerator(client=self._make_client())
        now = timezone.now()
        data = gen._collect_data(cutoff)
        context = gen._build_context_block(data, cutoff, now)

        assert "TOP METRIC CHANGES" in context


# ---------------------------------------------------------------------------
# WeeklyBriefing model
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestWeeklyBriefingModel:
    """Basic model-level tests."""

    def test_is_in_progress_true_for_pending(self):
        b = WeeklyBriefingFactory.build(status=WeeklyBriefing.Status.PENDING)
        assert b.is_in_progress is True

    def test_is_in_progress_true_for_generating(self):
        b = WeeklyBriefingFactory.build(status=WeeklyBriefing.Status.GENERATING)
        assert b.is_in_progress is True

    def test_is_in_progress_false_for_ready(self):
        b = WeeklyBriefingFactory.build(status=WeeklyBriefing.Status.READY)
        assert b.is_in_progress is False

    def test_str_includes_status(self, db):
        b = WeeklyBriefingFactory(status=WeeklyBriefing.Status.READY)
        assert "Ready" in str(b)


# ---------------------------------------------------------------------------
# GroqClient
# ---------------------------------------------------------------------------

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"


@pytest.mark.django_db
class TestGroqClient:
    """Tests for GroqClient — Groq cloud LLM wrapper."""

    def test_is_available_with_key(self):
        from briefings.generator import GroqClient

        client = GroqClient(api_key="test-key", model="llama-3.3-70b-versatile")
        assert client.is_available() is True

    def test_is_available_without_key(self):
        from briefings.generator import GroqClient

        client = GroqClient(api_key="", model="llama-3.3-70b-versatile")
        assert client.is_available() is False

    @resp.activate
    def test_chat_happy_path(self):
        from briefings.generator import GroqClient

        resp.add(
            resp.POST,
            GROQ_API_URL,
            json={"choices": [{"message": {"content": "Groq response"}}]},
            status=200,
        )
        client = GroqClient(api_key="test-key", model="test-model")
        result = client.chat("system prompt", "user prompt")
        assert result == "Groq response"

    @resp.activate
    def test_chat_http_error(self):
        import requests as req

        from briefings.generator import GroqClient

        resp.add(resp.POST, GROQ_API_URL, status=401)
        client = GroqClient(api_key="bad-key", model="test-model")
        with pytest.raises(req.HTTPError):
            client.chat("system", "user")


# ---------------------------------------------------------------------------
# get_llm_client factory
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGetLlmClient:
    """Tests for get_llm_client — LLM provider resolution."""

    def test_groq_provider(self):
        from briefings.generator import GroqClient, get_llm_client
        from briefings.models import BriefingConfig

        cfg = BriefingConfig.get()
        cfg.llm_provider = BriefingConfig.LLMProvider.GROQ
        cfg.groq_api_key = "test-key"
        cfg.save()
        client = get_llm_client(cfg)
        assert isinstance(client, GroqClient)

    def test_ollama_provider(self):
        from briefings.generator import OllamaClient, get_llm_client
        from briefings.models import BriefingConfig

        cfg = BriefingConfig.get()
        cfg.llm_provider = BriefingConfig.LLMProvider.OLLAMA
        cfg.save()
        client = get_llm_client(cfg)
        assert isinstance(client, OllamaClient)

    @resp.activate
    def test_auto_with_ollama_available(self):
        from briefings.generator import OllamaClient, get_llm_client
        from briefings.models import BriefingConfig

        resp.add(resp.GET, OLLAMA_TAGS_URL, json={"models": []}, status=200)
        cfg = BriefingConfig.get()
        cfg.llm_provider = BriefingConfig.LLMProvider.AUTO
        cfg.save()
        client = get_llm_client(cfg)
        assert isinstance(client, OllamaClient)

    @resp.activate
    def test_auto_ollama_down_falls_back_to_groq(self):
        from briefings.generator import GroqClient, get_llm_client
        from briefings.models import BriefingConfig

        resp.add(
            resp.GET,
            OLLAMA_TAGS_URL,
            body=requests.ConnectionError("connection refused"),
        )
        cfg = BriefingConfig.get()
        cfg.llm_provider = BriefingConfig.LLMProvider.AUTO
        cfg.groq_api_key = "fallback-key"
        cfg.save()
        client = get_llm_client(cfg)
        assert isinstance(client, GroqClient)

# dashboard/tests/test_primary_org.py
"""Tests for the PRIMARY_ORG_NAME / PRIMARY_ORG_DESCRIPTION settings.

The primary organisation's name must flow into UI labels (via the
``primary_org`` context processor) and into both LLM system prompts, so a
deployment tracking a different organisation never shows the demo name.
"""

from unittest.mock import MagicMock

import pytest
from django.contrib.auth.models import User
from django.test import Client, override_settings
from django.urls import reverse

from briefings.generator import _system_prompt
from metrics.delta import _llm_extract_competitors

ORG = {"PRIMARY_ORG_NAME": "Acme", "PRIMARY_ORG_DESCRIPTION": "a developer-tools company"}


@pytest.fixture
def client(db):
    """Authenticated client whose profile shows all entities."""
    user = User.objects.create_user(username="orguser", password="testpass")
    user.profile.show_all_entities = True
    user.profile.save(update_fields=["show_all_entities"])
    c = Client()
    c.login(username="orguser", password="testpass")
    return c


@override_settings(**ORG)
def test_dashboard_labels_use_primary_org_name(client):
    response = client.get(reverse("dashboard:main_dashboard"))
    content = response.content.decode()
    assert response.status_code == 200
    assert "Acme vs Competitors by Vertical" in content
    assert "Blockstream" not in content


@override_settings(**ORG)
def test_briefing_system_prompt_uses_primary_org():
    prompt = _system_prompt()
    assert "analyst for Acme, a developer-tools company." in prompt
    assert "Blockstream" not in prompt


@override_settings(**ORG)
def test_competitor_prompt_uses_primary_org():
    llm = MagicMock()
    llm.chat.return_value = "[]"
    _llm_extract_competitors(llm, [(1, "Title", "Content", "https://example.com/1")], set())
    system = llm.chat.call_args.kwargs["system"]
    assert "analyst for Acme, a developer-tools company." in system
    assert "competitive relevance to Acme" in system
    assert "Blockstream" not in system

# core/tests/test_seed.py
"""Tests for the seed management command."""

import json
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from core.models import Entity, Vertical

MINIMAL_ENTITIES = {
    "verticals": [
        {"name": "Test Vertical", "slug": "test-vertical"},
    ],
    "entities": [
        {
            "name": "Test Entity",
            "slug": "test-entity",
            "vertical": "test-vertical",
            "is_primary": True,
            "github_org": "test-org",
            "github_repo": "test-repo",
            "app_store_id": "",
            "news_keywords": ["test keyword"],
        }
    ],
}


@pytest.fixture
def entities_file(tmp_path: Path) -> Path:
    """Write minimal entities JSON to a temp file and return its path."""
    f = tmp_path / "entities.json"
    f.write_text(json.dumps(MINIMAL_ENTITIES), encoding="utf-8")
    return f


@pytest.mark.django_db
class TestSeedCommand:
    def test_creates_verticals_and_entities(self, entities_file):
        call_command("seed", file=entities_file)
        assert Vertical.objects.filter(slug="test-vertical").exists()
        assert Entity.objects.filter(slug="test-entity").exists()

    def test_idempotent(self, entities_file):
        """Running seed twice must not create duplicates."""
        call_command("seed", file=entities_file)
        call_command("seed", file=entities_file)
        assert Vertical.objects.filter(slug="test-vertical").count() == 1
        assert Entity.objects.filter(slug="test-entity").count() == 1

    def test_entity_fields_set_correctly(self, entities_file):
        call_command("seed", file=entities_file)
        entity = Entity.objects.get(slug="test-entity")
        assert entity.name == "Test Entity"
        assert entity.is_primary is True
        assert entity.github_org == "test-org"
        assert entity.news_keywords == ["test keyword"]
        assert entity.vertical.slug == "test-vertical"

    def test_missing_file_raises_error(self):
        with pytest.raises(CommandError, match="not found"):
            call_command("seed", file=Path("/nonexistent/entities.json"))

    def test_missing_vertical_skips_link(self, tmp_path):
        """Entity whose vertical slug doesn't exist gets vertical=None."""
        data = {
            "verticals": [],
            "entities": [
                {
                    "name": "Orphan Entity",
                    "slug": "orphan-entity",
                    "vertical": "nonexistent-vertical",
                    "is_primary": False,
                    "news_keywords": [],
                }
            ],
        }
        f = tmp_path / "entities.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        call_command("seed", file=f)  # must not raise
        entity = Entity.objects.get(slug="orphan-entity")
        assert entity.vertical is None

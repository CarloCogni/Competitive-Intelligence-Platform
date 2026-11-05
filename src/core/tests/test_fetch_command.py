# core/tests/test_fetch_command.py
"""Tests for the fetch management command."""

from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from core.tests.factories import EntityFactory


@pytest.mark.django_db
class TestFetchCommand:
    """Tests for the fetch management command."""

    @patch("scheduler.jobs._run_fetcher_job")
    def test_all_sources_all_entities(self, mock_run):
        call_command("fetch")
        # Should call each source's job function (7 sources)
        assert mock_run.call_count == 7

    @patch("scheduler.jobs._run_fetcher_job")
    def test_single_source(self, mock_run):
        call_command("fetch", "--source", "github")
        assert mock_run.call_count == 1

    def test_unknown_entity_raises(self):
        with pytest.raises(CommandError, match="No entity"):
            call_command("fetch", "--entity", "nonexistent")

    @patch("core.management.commands.fetch.Command._run_single_fetcher")
    @patch("metrics.delta.run_delta_detection")
    def test_single_entity(self, mock_delta, mock_fetcher):
        EntityFactory(slug="test-entity")
        call_command("fetch", "--source", "github", "--entity", "test-entity")
        mock_fetcher.assert_called_once()
        mock_delta.assert_called_once_with("github")

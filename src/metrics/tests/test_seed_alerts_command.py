# metrics/tests/test_seed_alerts_command.py
"""Tests for the seed_alerts management command."""

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from core.tests.factories import EntityFactory
from metrics.models import Alert


@pytest.mark.django_db
class TestSeedAlertsCommand:
    """Tests for seed_alerts management command."""

    def test_default_creates_20_alerts(self):
        EntityFactory(github_org="org", news_keywords=["kw"], app_store_id="123")
        call_command("seed_alerts")
        assert Alert.objects.count() == 90

    def test_custom_count(self):
        EntityFactory(github_org="org")
        call_command("seed_alerts", "--count", "5")
        assert Alert.objects.count() == 5

    def test_clear_deletes_all(self):
        EntityFactory(github_org="org")
        call_command("seed_alerts", "--count", "10")
        assert Alert.objects.count() == 10
        call_command("seed_alerts", "--clear")
        assert Alert.objects.count() == 0

    def test_clear_then_seed(self):
        EntityFactory(github_org="org")
        call_command("seed_alerts", "--count", "5")
        call_command("seed_alerts", "--clear", "--count", "3")
        assert Alert.objects.count() == 3

    def test_no_entities_raises(self):
        with pytest.raises(CommandError, match="No entities"):
            call_command("seed_alerts")

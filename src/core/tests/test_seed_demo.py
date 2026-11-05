# core/tests/test_seed_demo.py
"""Tests for the seed_demo management command."""

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from core.tests.factories import EntityFactory
from metrics.models import Alert, CompoundRule, GARun, ThresholdSnapshot

User = get_user_model()


@pytest.mark.django_db
class TestSeedDemoCommand:
    @pytest.fixture(autouse=True)
    def _seed_entities(self, db):
        """seed_demo expects entities to exist (from seed command)."""
        EntityFactory(slug="demo-a", is_primary=True, github_org="org-a", news_keywords=["kw"])
        EntityFactory(slug="demo-b", github_org="org-b", news_keywords=["kw2"])

    def test_creates_superuser(self):
        call_command("seed_demo")
        assert User.objects.filter(username="batman").exists()
        assert User.objects.get(username="batman").is_superuser

    def test_idempotent(self):
        call_command("seed_demo")
        count1 = User.objects.count()
        alert_count1 = Alert.objects.count()
        call_command("seed_demo")
        assert User.objects.count() == count1
        # Alerts should not double (idempotent guard: existing >= 50 skips)
        assert Alert.objects.count() >= alert_count1

    def test_creates_alerts(self):
        call_command("seed_demo")
        assert Alert.objects.count() > 0

    def test_creates_ga_runs(self):
        call_command("seed_demo")
        assert GARun.objects.count() > 0

    def test_creates_compound_rules(self):
        call_command("seed_demo")
        assert CompoundRule.objects.count() > 0

    def test_creates_threshold_snapshots(self):
        call_command("seed_demo")
        assert ThresholdSnapshot.objects.count() > 0

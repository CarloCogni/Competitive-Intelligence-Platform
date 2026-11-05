# metrics/tests/test_seed_demo_commands.py
"""Tests for demo seeding commands: seed_demo_alerts, seed_demo_metrics, seed_threshold_history."""

import pytest
from django.core.management import call_command

from core.tests.factories import EntityFactory
from metrics.models import Alert, Metric, ThresholdSnapshot


@pytest.mark.django_db
class TestSeedDemoAlertsCommand:
    @pytest.fixture(autouse=True)
    def _entities(self, db):
        EntityFactory(slug="demo-a", is_primary=True, github_org="org", news_keywords=["kw"])
        EntityFactory(slug="demo-b", github_org="org2")

    def test_creates_spike_alerts(self):
        call_command(
            "seed_demo_alerts", "--spike-count", "10", "--compound-count", "0", "--seen-count", "0"
        )
        seeded = [
            a
            for a in Alert.objects.all()
            if isinstance(a.metadata, dict) and a.metadata.get("seeded")
        ]
        assert len(seeded) == 10

    def test_clear_removes_seeded(self):
        call_command(
            "seed_demo_alerts", "--spike-count", "5", "--compound-count", "0", "--seen-count", "0"
        )
        assert Alert.objects.count() > 0
        call_command("seed_demo_alerts", "--clear", "--no-seed")
        seeded = [
            a
            for a in Alert.objects.all()
            if isinstance(a.metadata, dict) and a.metadata.get("seeded")
        ]
        assert len(seeded) == 0

    def test_idempotent_guard(self, capsys):
        call_command(
            "seed_demo_alerts", "--spike-count", "5", "--compound-count", "0", "--seen-count", "0"
        )
        call_command(
            "seed_demo_alerts", "--spike-count", "5", "--compound-count", "0", "--seen-count", "0"
        )
        captured = capsys.readouterr()
        assert "already exist" in captured.out


@pytest.mark.django_db
class TestSeedDemoMetricsCommand:
    @pytest.fixture(autouse=True)
    def _entities(self, db):
        # seed_demo_metrics requires specific slugs from _ENTITY_BASELINES
        EntityFactory(slug="jade", github_org="org-jade")
        EntityFactory(slug="green-wallet", github_org="org-green")

    def test_creates_metrics(self):
        call_command("seed_demo_metrics", "--days", "5")
        assert Metric.objects.filter(source="seeded").count() > 0

    def test_idempotent_guard(self, capsys):
        call_command("seed_demo_metrics", "--days", "5")
        call_command("seed_demo_metrics", "--days", "5")
        captured = capsys.readouterr()
        assert "already exist" in captured.out.lower()

    def test_clear_and_no_seed(self):
        call_command("seed_demo_metrics", "--days", "5")
        assert Metric.objects.filter(source="seeded").count() > 0
        call_command("seed_demo_metrics", "--clear", "--no-seed")
        assert Metric.objects.filter(source="seeded").count() == 0


@pytest.mark.django_db
class TestSeedThresholdHistoryCommand:
    @pytest.fixture(autouse=True)
    def _entities(self, db):
        EntityFactory(slug="demo-th", github_org="org")

    def test_creates_snapshots(self):
        call_command("seed_threshold_history")
        assert ThresholdSnapshot.objects.count() > 0

    def test_clear_then_reseed(self):
        call_command("seed_threshold_history")
        first_count = ThresholdSnapshot.objects.count()
        assert first_count > 0
        # --clear removes then re-seeds in same call
        call_command("seed_threshold_history", "--clear")
        assert ThresholdSnapshot.objects.count() == first_count

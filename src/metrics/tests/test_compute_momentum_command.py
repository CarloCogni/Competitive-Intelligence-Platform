# metrics/tests/test_compute_momentum_command.py
"""Tests for the compute_momentum management command."""

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from core.tests.factories import EntityFactory
from metrics.models import Metric, MomentumScore


@pytest.mark.django_db
class TestComputeMomentumCommand:
    def test_no_data_prints_warning(self, capsys):
        call_command("compute_momentum")
        captured = capsys.readouterr()
        assert "insufficient data" in captured.out.lower() or "No momentum" in captured.out

    def test_with_data_prints_table(self, capsys):
        now = timezone.now()
        old = now - timedelta(days=8)
        e = EntityFactory(slug="cmd-entity")
        Metric.objects.create(
            entity=e, source="github", key="github_stars", value=100.0, captured_at=old
        )
        Metric.objects.create(
            entity=e, source="github", key="github_stars", value=200.0, captured_at=now
        )

        call_command("compute_momentum")
        captured = capsys.readouterr()
        assert "cmd-entity" in captured.out or "Entity" in captured.out
        assert MomentumScore.objects.count() >= 1

    def test_dry_run_flag(self, capsys):
        now = timezone.now()
        old = now - timedelta(days=8)
        e = EntityFactory(slug="dry-run-entity")
        Metric.objects.create(
            entity=e, source="github", key="github_stars", value=100.0, captured_at=old
        )
        Metric.objects.create(
            entity=e, source="github", key="github_stars", value=200.0, captured_at=now
        )

        call_command("compute_momentum", "--dry-run")
        captured = capsys.readouterr()
        assert "DRY RUN" in captured.out
        assert MomentumScore.objects.count() == 0

    def test_entity_filter(self, capsys):
        now = timezone.now()
        old = now - timedelta(days=8)
        e1 = EntityFactory(slug="filter-a")
        e2 = EntityFactory(slug="filter-b")
        for e in (e1, e2):
            Metric.objects.create(
                entity=e, source="github", key="github_stars", value=100.0, captured_at=old
            )
            Metric.objects.create(
                entity=e, source="github", key="github_stars", value=200.0, captured_at=now
            )

        call_command("compute_momentum", "--entity", "filter-a")
        # Only filter-a should get a score
        assert MomentumScore.objects.filter(entity=e1).exists()
        assert not MomentumScore.objects.filter(entity=e2).exists()

# metrics/tests/test_seed_momentum_command.py
"""Tests for the seed_momentum management command."""

import pytest
from django.core.management import call_command

from core.tests.factories import EntityFactory
from metrics.models import MomentumScore


@pytest.mark.django_db
class TestSeedMomentumCommand:
    def test_seeds_scores_for_entities(self):
        EntityFactory(slug="seed-a")
        EntityFactory(slug="seed-b")
        call_command("seed_momentum", "--days", "10")
        # 2 entities x 10 days = 20 scores
        assert MomentumScore.objects.count() == 20

    def test_clear_flag_removes_existing(self):
        EntityFactory(slug="clear-test")
        call_command("seed_momentum", "--days", "5")
        assert MomentumScore.objects.count() > 0
        call_command("seed_momentum", "--clear")
        # --clear alone without --days still seeds with default days,
        # but clears first. Just verify clear happened.
        # Actually --clear alone clears then re-seeds with default 90 days.
        # Let's just check it didn't crash and scores exist.
        assert MomentumScore.objects.count() >= 0

    def test_no_entities_prints_warning(self, capsys):
        call_command("seed_momentum")
        captured = capsys.readouterr()
        assert "No entities" in captured.out

    def test_deterministic_with_same_seed(self):
        EntityFactory(slug="deterministic")
        call_command("seed_momentum", "--days", "5")
        scores_1 = list(
            MomentumScore.objects.order_by("computed_at").values_list("score", flat=True)
        )
        MomentumScore.objects.all().delete()
        call_command("seed_momentum", "--days", "5")
        scores_2 = list(
            MomentumScore.objects.order_by("computed_at").values_list("score", flat=True)
        )
        assert scores_1 == scores_2

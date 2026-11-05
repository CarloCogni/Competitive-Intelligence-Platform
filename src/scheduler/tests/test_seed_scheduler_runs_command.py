# scheduler/tests/test_seed_scheduler_runs_command.py
"""Tests for the seed_scheduler_runs management command."""

import pytest
from django.core.management import call_command

from scheduler.models import BatchRun, FetcherConfig


@pytest.mark.django_db
class TestSeedSchedulerRunsCommand:
    @pytest.fixture(autouse=True)
    def _seed_configs(self, db):
        """Ensure at least one enabled FetcherConfig exists."""
        FetcherConfig.objects.get_or_create(
            source="github",
            defaults={"interval_hours": 24, "enabled": True},
        )

    def test_seeds_runs_for_enabled_fetchers(self):
        call_command("seed_scheduler_runs", "--days", "3")
        assert BatchRun.objects.count() > 0

    def test_clear_removes_seeded_runs(self):
        call_command("seed_scheduler_runs", "--days", "3")
        assert BatchRun.objects.count() > 0
        call_command("seed_scheduler_runs", "--clear")
        assert BatchRun.objects.count() == 0

    def test_rerun_skips_existing_slots(self):
        call_command("seed_scheduler_runs", "--days", "3")
        count_1 = BatchRun.objects.count()
        call_command("seed_scheduler_runs", "--days", "3")
        count_2 = BatchRun.objects.count()
        # Most slots already exist; at most a handful of new ones from time progression
        new_records = count_2 - count_1
        assert new_records < count_1

# scheduler/tests/test_models.py
"""Tests for scheduler models: FetcherConfig and BatchRun."""

from datetime import timedelta

import pytest
from django.utils import timezone

from scheduler.tests.factories import BatchRunFactory, FetcherConfigFactory


@pytest.mark.django_db
class TestFetcherConfigStr:
    """Tests for FetcherConfig.__str__."""

    def test_enabled_display(self):
        cfg = FetcherConfigFactory(source="github", interval_hours=24, enabled=True)
        result = str(cfg)
        assert "GitHub" in result
        assert "24h" in result
        assert "enabled" in result

    def test_disabled_display(self):
        cfg = FetcherConfigFactory(source="news", interval_hours=2)
        cfg.enabled = False
        cfg.save(update_fields=["enabled"])
        result = str(cfg)
        assert "disabled" in result


@pytest.mark.django_db
class TestBatchRunStr:
    """Tests for BatchRun.__str__."""

    def test_str_includes_job_id_and_status(self):
        run = BatchRunFactory(job_id="github_fetcher", status="success")
        result = str(run)
        assert "github_fetcher" in result
        assert "Success" in result


@pytest.mark.django_db
class TestBatchRunDurationSeconds:
    """Tests for BatchRun.duration_seconds property."""

    def test_with_both_timestamps(self):
        now = timezone.now()
        run = BatchRunFactory(started_at=now, finished_at=now + timedelta(seconds=45))
        assert run.duration_seconds == pytest.approx(45.0, abs=0.1)

    def test_missing_finished_at_returns_none(self):
        run = BatchRunFactory(finished_at=None)
        assert run.duration_seconds is None

    def test_missing_started_at_returns_none(self):
        run = BatchRunFactory(started_at=None, finished_at=None)
        assert run.duration_seconds is None

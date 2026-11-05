# briefings/tests/test_models.py
"""Tests for briefing models: WeeklyBriefing, BriefingConfig, EmailConfig."""

import pytest
from django.utils import timezone

from briefings.models import BriefingConfig, EmailConfig, WeeklyBriefing


@pytest.mark.django_db
class TestWeeklyBriefingStr:
    """Tests for WeeklyBriefing.__str__."""

    def test_str_includes_date_and_status(self):
        briefing = WeeklyBriefing.objects.create(
            period_start=timezone.now(),
            period_end=timezone.now(),
            status=WeeklyBriefing.Status.READY,
        )
        result = str(briefing)
        assert "Ready" in result


@pytest.mark.django_db
class TestWeeklyBriefingIsInProgress:
    """Tests for WeeklyBriefing.is_in_progress property."""

    def _make(self, status):
        return WeeklyBriefing.objects.create(
            period_start=timezone.now(),
            period_end=timezone.now(),
            status=status,
        )

    def test_pending_is_in_progress(self):
        assert self._make(WeeklyBriefing.Status.PENDING).is_in_progress is True

    def test_generating_is_in_progress(self):
        assert self._make(WeeklyBriefing.Status.GENERATING).is_in_progress is True

    def test_ready_is_not_in_progress(self):
        assert self._make(WeeklyBriefing.Status.READY).is_in_progress is False

    def test_failed_is_not_in_progress(self):
        assert self._make(WeeklyBriefing.Status.FAILED).is_in_progress is False


@pytest.mark.django_db
class TestBriefingConfigSingleton:
    """Tests for BriefingConfig singleton pattern."""

    def test_save_enforces_pk_1(self):
        cfg = BriefingConfig(llm_provider="ollama", period_days=14)
        cfg.save()
        assert cfg.pk == 1

    def test_get_creates_with_defaults(self):
        cfg = BriefingConfig.get()
        assert cfg.pk == 1
        assert cfg.period_days == 7  # default

    def test_second_save_overwrites(self):
        BriefingConfig(period_days=7).save()
        BriefingConfig(period_days=30).save()
        assert BriefingConfig.objects.count() == 1
        assert BriefingConfig.objects.first().period_days == 30


@pytest.mark.django_db
class TestEmailConfigSingleton:
    """Tests for EmailConfig singleton pattern."""

    def test_save_enforces_pk_1(self):
        cfg = EmailConfig(smtp_host="smtp.example.com")
        cfg.save()
        assert cfg.pk == 1

    def test_get_creates_with_defaults(self):
        cfg = EmailConfig.get()
        assert cfg.pk == 1

    def test_is_configured_true(self):
        cfg = EmailConfig(smtp_host="smtp.example.com")
        cfg.save()
        assert cfg.is_configured is True

    def test_is_configured_false(self):
        cfg = EmailConfig(smtp_host="")
        cfg.save()
        assert cfg.is_configured is False

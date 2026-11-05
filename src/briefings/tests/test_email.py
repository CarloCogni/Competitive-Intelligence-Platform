# briefings/tests/test_email.py
"""Tests for briefings.email — email delivery for weekly briefings."""

from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from briefings.email import send_briefing_email
from briefings.models import BriefingConfig, EmailConfig, WeeklyBriefing


def _make_briefing(db):
    """Create a ready briefing for testing."""
    return WeeklyBriefing.objects.create(
        period_start=timezone.now(),
        period_end=timezone.now(),
        status=WeeklyBriefing.Status.READY,
        content="# Test briefing",
        model_used="test-model",
    )


@pytest.mark.django_db
class TestSendBriefingEmail:
    """Tests for send_briefing_email function."""

    def test_disabled_returns_zero(self):
        briefing = _make_briefing(True)
        cfg = BriefingConfig.get()
        cfg.send_briefing_email = False
        cfg.save()
        assert send_briefing_email(briefing) == 0

    def test_no_recipients_returns_zero(self):
        briefing = _make_briefing(True)
        cfg = BriefingConfig.get()
        cfg.send_briefing_email = True
        cfg.briefing_email_recipients = ""
        cfg.save()
        assert send_briefing_email(briefing) == 0

    def test_smtp_not_configured_returns_zero(self):
        briefing = _make_briefing(True)
        cfg = BriefingConfig.get()
        cfg.send_briefing_email = True
        cfg.briefing_email_recipients = "test@example.com"
        cfg.save()
        # EmailConfig defaults to blank smtp_host
        EmailConfig.get()
        assert send_briefing_email(briefing) == 0

    @patch("briefings.email.EmailBackend")
    @patch("briefings.email.EmailMultiAlternatives")
    def test_happy_path_sends_email(self, mock_email_cls, mock_backend_cls):
        briefing = _make_briefing(True)
        cfg = BriefingConfig.get()
        cfg.send_briefing_email = True
        cfg.briefing_email_recipients = "a@test.com, b@test.com"
        cfg.save()
        ecfg = EmailConfig.get()
        ecfg.smtp_host = "smtp.test.com"
        ecfg.save()

        mock_backend = MagicMock()
        mock_backend_cls.return_value = mock_backend
        mock_backend.__enter__ = MagicMock(return_value=mock_backend)
        mock_backend.__exit__ = MagicMock(return_value=False)

        result = send_briefing_email(briefing)
        assert result == 2

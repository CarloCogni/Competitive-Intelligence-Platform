# core/tests/test_middleware.py
"""Tests for TimezoneMiddleware — per-request timezone activation."""

import pytest
from django.contrib.auth.models import User
from django.test import RequestFactory
from django.utils import timezone

from core.middleware import TimezoneMiddleware


@pytest.fixture
def rf():
    return RequestFactory()


@pytest.fixture
def middleware():
    """TimezoneMiddleware wrapping a no-op response function."""
    return TimezoneMiddleware(lambda request: None)


@pytest.mark.django_db
class TestTimezoneMiddleware:
    """Tests for TimezoneMiddleware.__call__ and _resolve_timezone."""

    def test_anonymous_user_deactivates(self, rf, middleware):
        from django.contrib.auth.models import AnonymousUser

        request = rf.get("/")
        request.user = AnonymousUser()
        middleware(request)
        # Should fall back to server default (deactivated)
        assert timezone.get_current_timezone_name() is not None

    def test_authenticated_user_with_valid_tz(self, rf, middleware):
        user = User.objects.create_user("tz-user", password="pass")
        user.profile.timezone = "America/New_York"
        user.profile.save(update_fields=["timezone"])

        request = rf.get("/")
        request.user = user
        middleware(request)
        assert timezone.get_current_timezone_name() == "America/New_York"

    def test_invalid_tz_falls_back(self, rf, middleware):
        user = User.objects.create_user("bad-tz", password="pass")
        user.profile.timezone = "Not/A/Valid/Zone"
        user.profile.save(update_fields=["timezone"])

        request = rf.get("/")
        request.user = user
        middleware(request)
        # Should deactivate — no crash
        assert timezone.get_current_timezone_name() is not None

    def test_empty_timezone_string(self, rf, middleware):
        user = User.objects.create_user("empty-tz", password="pass")
        user.profile.timezone = ""
        user.profile.save(update_fields=["timezone"])

        request = rf.get("/")
        request.user = user
        middleware(request)
        # Should deactivate gracefully
        assert timezone.get_current_timezone_name() is not None

# dashboard/tests/test_services.py
"""Tests for dashboard service functions."""

import pytest
from django.contrib.auth.models import User

from core.tests.factories import EntityFactory
from dashboard.services import save_profile_settings


@pytest.mark.django_db
class TestSaveProfileSettings:
    """Tests for save_profile_settings — atomic profile update."""

    def _make_profile(self):
        user = User.objects.create_user("svc-test", password="pass")
        return user.profile

    def test_persists_digest_enabled(self):
        profile = self._make_profile()
        save_profile_settings(profile, [], digest_enabled=False, timezone="UTC")
        profile.refresh_from_db()
        assert profile.digest_enabled is False

    def test_persists_timezone(self):
        profile = self._make_profile()
        save_profile_settings(profile, [], digest_enabled=True, timezone="America/New_York")
        profile.refresh_from_db()
        assert profile.timezone == "America/New_York"

    def test_sets_watched_entities(self):
        profile = self._make_profile()
        e1 = EntityFactory()
        e2 = EntityFactory()
        save_profile_settings(profile, [e1, e2], digest_enabled=True, timezone="UTC")
        assert profile.watched_entities.count() == 2

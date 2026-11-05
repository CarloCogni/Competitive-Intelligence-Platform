# dashboard/tests/test_forms.py
"""Tests for dashboard forms."""

import pytest

from core.tests.factories import EntityFactory
from dashboard.forms import UserProfileForm


@pytest.mark.django_db
class TestUserProfileForm:
    """Tests for UserProfileForm validation."""

    def test_valid_data(self):
        e = EntityFactory()
        form = UserProfileForm(
            data={
                "watched_entities": [e.pk],
                "digest_enabled": True,
                "timezone": "UTC",
            }
        )
        assert form.is_valid()

    def test_invalid_timezone_rejected(self):
        form = UserProfileForm(
            data={
                "digest_enabled": True,
                "timezone": "Not/A/Valid/Zone",
            }
        )
        assert not form.is_valid()
        assert "timezone" in form.errors

    def test_empty_watched_entities_valid(self):
        form = UserProfileForm(
            data={
                "digest_enabled": False,
                "timezone": "America/New_York",
            }
        )
        assert form.is_valid()

    def test_missing_timezone_invalid(self):
        form = UserProfileForm(
            data={
                "digest_enabled": True,
            }
        )
        assert not form.is_valid()
        assert "timezone" in form.errors

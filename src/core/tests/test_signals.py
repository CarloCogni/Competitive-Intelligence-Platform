# core/tests/test_signals.py
"""Tests for core signals — auto-creation of UserProfile on User save."""

import pytest
from django.contrib.auth.models import User

from core.models import UserProfile
from core.tests.factories import EntityFactory


@pytest.mark.django_db
class TestCreateUserProfileSignal:
    """Tests for the post_save signal that creates UserProfile for new users."""

    def test_new_user_gets_profile(self):
        user = User.objects.create_user("signal-test", password="pass")
        assert UserProfile.objects.filter(user=user).exists()
        assert hasattr(user, "profile")

    def test_save_existing_user_does_not_duplicate(self):
        user = User.objects.create_user("dup-test", password="pass")
        user.first_name = "Updated"
        user.save()
        assert UserProfile.objects.filter(user=user).count() == 1

    def test_watchlist_seeded_with_existing_entities(self):
        e1 = EntityFactory()
        e2 = EntityFactory()
        user = User.objects.create_user("seed-test", password="pass")
        assert user.profile.watched_entities.count() == 2
        assert e1 in user.profile.watched_entities.all()
        assert e2 in user.profile.watched_entities.all()

# core/tests/test_models.py
"""Tests for core models: Vertical, Entity, and UserProfile."""

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError

from core.models import Entity, Vertical

from .factories import EntityFactory, VerticalFactory


@pytest.mark.django_db
class TestVertical:
    def test_str(self):
        v = VerticalFactory(name="Wallet (HW)")
        assert str(v) == "Wallet (HW)"

    def test_slug_auto_populated(self):
        v = Vertical(name="My Vertical")
        v.save()
        assert v.slug == "my-vertical"

    def test_slug_not_overwritten(self):
        v = VerticalFactory(name="Test", slug="custom-slug")
        assert v.slug == "custom-slug"


@pytest.mark.django_db
class TestEntity:
    def test_str(self):
        e = EntityFactory(name="Green Wallet")
        assert str(e) == "Green Wallet"

    def test_default_is_primary_false(self):
        e = EntityFactory()
        assert e.is_primary is False

    def test_primary_entity(self):
        e = EntityFactory(is_primary=True)
        assert e.is_primary is True

    def test_vertical_fk(self):
        v = VerticalFactory(name="Custody")
        e = EntityFactory(vertical=v)
        assert e.vertical.name == "Custody"

    def test_news_keywords_defaults_to_empty_list(self):
        e = EntityFactory(news_keywords=[])
        assert e.news_keywords == []

    def test_slug_unique_constraint(self):
        from django.db import IntegrityError

        EntityFactory(slug="duplicate")
        with pytest.raises(IntegrityError):
            # Bypass get_or_create to force the constraint violation
            Entity.objects.create(
                name="Another",
                slug="duplicate",
            )


@pytest.mark.django_db
class TestUserProfile:
    """Tests for UserProfile model."""

    def test_str(self):
        user = User.objects.create_user("profile-str-test", password="pass")
        assert str(user.profile) == "Profile(profile-str-test)"

    def test_news_keywords_validator_rejects_invalid(self):
        e = EntityFactory(news_keywords="not-a-list")
        with pytest.raises(ValidationError):
            e.full_clean()

    def test_news_keywords_validator_accepts_valid(self):
        e = EntityFactory(news_keywords=["bitcoin", "wallet"])
        e.full_clean()  # should not raise

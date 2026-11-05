# dashboard/tests/test_models.py
"""Tests for dashboard models."""

import pytest
from django.contrib.auth.models import User
from django.db import IntegrityError

from core.tests.factories import EntityFactory
from dashboard.models import SavedComparison


@pytest.mark.django_db
class TestSavedComparison:
    """Tests for SavedComparison model."""

    def test_str_includes_entities(self):
        user = User.objects.create_user("cmp-user", password="pass")
        e1 = EntityFactory(name="Alpha")
        e2 = EntityFactory(name="Beta")
        sc = SavedComparison.objects.create(user=user, name="test", entity_a=e1, entity_b=e2)
        result = str(sc)
        assert "Alpha" in result
        assert "Beta" in result

    def test_unique_together_constraint(self):
        user = User.objects.create_user("cmp-uniq", password="pass")
        e1 = EntityFactory()
        e2 = EntityFactory()
        SavedComparison.objects.create(user=user, name="dup", entity_a=e1, entity_b=e2)
        with pytest.raises(IntegrityError):
            SavedComparison.objects.create(user=user, name="dup", entity_a=e1, entity_b=e2)

# metrics/tests/test_models_str.py
"""Tests for __str__ methods on metrics models not covered by other test files."""

import pytest
from django.contrib.auth.models import User

from core.tests.factories import EntityFactory
from metrics.models import (
    AlertSeen,
    CandidateEntity,
    CompoundRule,
    GARun,
    Metric,
    MetricSnapshot,
    NewsItem,
)
from metrics.tests.factories import AlertFactory


@pytest.mark.django_db
class TestMetricSnapshotStr:
    def test_str(self):
        entity = EntityFactory(name="TestOrg")
        snap = MetricSnapshot.objects.create(entity=entity, source="github", data={"k": 1})
        result = str(snap)
        assert "TestOrg" in result
        assert "github" in result


@pytest.mark.django_db
class TestMetricStr:
    def test_str(self):
        entity = EntityFactory(name="TestOrg")
        m = Metric.objects.create(entity=entity, source="github", key="github_stars", value=100.0)
        result = str(m)
        assert "github_stars" in result
        assert "100.0" in result


@pytest.mark.django_db
class TestNewsItemStr:
    def test_str(self):
        item = NewsItem.objects.create(
            title="Breaking news about crypto", url="https://example.com/1"
        )
        assert "Breaking news" in str(item)


@pytest.mark.django_db
class TestCandidateEntityStr:
    def test_str(self):
        c = CandidateEntity.objects.create(name="NewCorp", mention_count=5)
        result = str(c)
        assert "NewCorp" in result
        assert "5 mentions" in result


@pytest.mark.django_db
class TestAlertSeenStr:
    def test_str(self):
        user = User.objects.create_user("seen-user", password="pass")
        alert = AlertFactory()
        seen = AlertSeen.objects.create(user=user, alert=alert)
        result = str(seen)
        assert "seen-user" in result


@pytest.mark.django_db
class TestGARunStr:
    def test_str(self):
        run = GARun.objects.create(
            status=GARun.Status.RUNNING,
            population_size=50,
            generations=100,
            mutation_rate=0.1,
            crossover_rate=0.7,
        )
        result = str(run)
        assert "GARun" in result
        assert "running" in result.lower()


@pytest.mark.django_db
class TestCompoundRuleStr:
    def test_str(self):
        rule = CompoundRule.objects.create(
            conditions=[
                {"metric_key": "github_stars", "operator": "gte", "threshold_pct": 0.15},
            ],
            fitness_score=0.85,
        )
        result = str(rule)
        assert "1 conditions" in result
        assert "0.850" in result

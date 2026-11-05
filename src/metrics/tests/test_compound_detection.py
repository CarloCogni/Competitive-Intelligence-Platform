# metrics/tests/test_compound_detection.py
"""Integration tests for compound detection in metrics/delta.py."""

from datetime import timedelta

import pytest
from django.utils import timezone

from core.tests.factories import EntityFactory
from metrics.delta import run_compound_detection
from metrics.models import Alert, CompoundRule, GARun, Metric


@pytest.fixture
def ga_run(db):
    """A completed GARun for linking CompoundRules."""
    return GARun.objects.create(
        status=GARun.Status.SUCCESS,
        population_size=40,
        generations=20,
        mutation_rate=0.3,
        crossover_rate=0.7,
        best_fitness=0.85,
        rules_produced=1,
    )


@pytest.fixture
def entity_with_metrics(db):
    """An entity with metric data spanning the lookback window."""
    entity = EntityFactory()
    now = timezone.now()

    # Baseline readings (8 days ago) and latest readings (now)
    for key, baseline_val, latest_val in [
        ("github_stars", 1000, 1300),  # +30%
        ("news_article_count", 5, 16),  # +220%
        ("github_forks", 200, 210),  # +5%
    ]:
        Metric.objects.create(
            entity=entity,
            source="github",
            key=key,
            value=baseline_val,
            captured_at=now - timedelta(days=8),
        )
        Metric.objects.create(
            entity=entity,
            source="github",
            key=key,
            value=latest_val,
            captured_at=now,
        )

    return entity


@pytest.mark.django_db
class TestRunCompoundDetection:
    """Tests for run_compound_detection()."""

    def test_compound_rule_fires_alert(self, entity_with_metrics, ga_run):
        """An active CompoundRule fires a COMPOUND_SPIKE alert when all conditions met."""
        rule = CompoundRule.objects.create(
            name="Stars + News",
            conditions=[
                {"metric_key": "github_stars", "operator": "gte", "threshold_pct": 0.20},
                {"metric_key": "news_article_count", "operator": "gte", "threshold_pct": 1.00},
            ],
            fitness_score=0.85,
            is_active=True,
            created_by_run=ga_run,
        )

        count = run_compound_detection()

        assert count == 1
        alert = Alert.objects.get(
            entity=entity_with_metrics,
            alert_type=Alert.AlertType.COMPOUND_SPIKE,
        )
        assert alert.metadata["rule_id"] == rule.pk
        assert alert.metadata["rule_name"] == "Stars + News"
        assert len(alert.metadata["conditions"]) == 2
        assert alert.metadata["severity"] in ("LOW", "MEDIUM", "HIGH")
        assert "Compound pattern" in alert.message

    def test_rule_does_not_fire_when_condition_unmet(self, entity_with_metrics, ga_run):
        """Rule does not fire when one condition's threshold is not met."""
        CompoundRule.objects.create(
            name="Stars + Forks",
            conditions=[
                {"metric_key": "github_stars", "operator": "gte", "threshold_pct": 0.20},
                # forks only went up 5%, threshold is 50%
                {"metric_key": "github_forks", "operator": "gte", "threshold_pct": 0.50},
            ],
            fitness_score=0.70,
            is_active=True,
            created_by_run=ga_run,
        )

        count = run_compound_detection()
        assert count == 0
        assert not Alert.objects.filter(
            alert_type=Alert.AlertType.COMPOUND_SPIKE,
        ).exists()

    def test_inactive_rule_skipped(self, entity_with_metrics, ga_run):
        """Inactive rules are not evaluated."""
        CompoundRule.objects.create(
            name="Stars + News (inactive)",
            conditions=[
                {"metric_key": "github_stars", "operator": "gte", "threshold_pct": 0.20},
                {"metric_key": "news_article_count", "operator": "gte", "threshold_pct": 1.00},
            ],
            fitness_score=0.85,
            is_active=False,
            created_by_run=ga_run,
        )

        count = run_compound_detection()
        assert count == 0

    def test_deduplication_prevents_double_fire(self, entity_with_metrics, ga_run):
        """Same rule + entity does not fire twice within the lookback window."""
        rule = CompoundRule.objects.create(
            name="Stars + News",
            conditions=[
                {"metric_key": "github_stars", "operator": "gte", "threshold_pct": 0.20},
                {"metric_key": "news_article_count", "operator": "gte", "threshold_pct": 1.00},
            ],
            fitness_score=0.85,
            is_active=True,
            created_by_run=ga_run,
        )

        # First run
        count1 = run_compound_detection()
        assert count1 == 1

        # Second run — should be deduped
        count2 = run_compound_detection()
        assert count2 == 0

        # Only one alert total
        assert (
            Alert.objects.filter(
                alert_type=Alert.AlertType.COMPOUND_SPIKE,
                metadata__rule_id=rule.pk,
            ).count()
            == 1
        )

    def test_no_active_rules_returns_zero(self, db):
        """Returns 0 when no active CompoundRules exist."""
        count = run_compound_detection()
        assert count == 0

    def test_missing_metric_data_does_not_crash(self, ga_run):
        """Entity with no metric data is gracefully skipped."""
        entity = EntityFactory()
        CompoundRule.objects.create(
            name="Stars + News",
            conditions=[
                {"metric_key": "github_stars", "operator": "gte", "threshold_pct": 0.20},
                {"metric_key": "news_article_count", "operator": "gte", "threshold_pct": 1.00},
            ],
            fitness_score=0.85,
            is_active=True,
            created_by_run=ga_run,
        )

        count = run_compound_detection()
        assert count == 0

    def test_lte_operator_detects_drops(self, ga_run):
        """lte operator detects negative deltas (drops)."""
        entity = EntityFactory()
        now = timezone.now()

        # appstore_rating dropped from 4.5 to 3.5 (−22%)
        Metric.objects.create(
            entity=entity,
            source="appstore",
            key="appstore_rating",
            value=4.5,
            captured_at=now - timedelta(days=8),
        )
        Metric.objects.create(
            entity=entity,
            source="appstore",
            key="appstore_rating",
            value=3.5,
            captured_at=now,
        )
        # github_stars went up 25%
        Metric.objects.create(
            entity=entity,
            source="github",
            key="github_stars",
            value=1000,
            captured_at=now - timedelta(days=8),
        )
        Metric.objects.create(
            entity=entity,
            source="github",
            key="github_stars",
            value=1250,
            captured_at=now,
        )

        CompoundRule.objects.create(
            name="Stars up + Rating drop",
            conditions=[
                {"metric_key": "github_stars", "operator": "gte", "threshold_pct": 0.20},
                {"metric_key": "appstore_rating", "operator": "lte", "threshold_pct": 0.10},
            ],
            fitness_score=0.75,
            is_active=True,
            created_by_run=ga_run,
        )

        count = run_compound_detection()
        assert count == 1
        alert = Alert.objects.get(alert_type=Alert.AlertType.COMPOUND_SPIKE)
        assert len(alert.metadata["conditions"]) == 2

    def test_alert_metadata_contains_actual_deltas(self, entity_with_metrics, ga_run):
        """Alert metadata includes actual_delta_pct for each condition."""
        CompoundRule.objects.create(
            name="Stars + News",
            conditions=[
                {"metric_key": "github_stars", "operator": "gte", "threshold_pct": 0.20},
                {"metric_key": "news_article_count", "operator": "gte", "threshold_pct": 1.00},
            ],
            fitness_score=0.85,
            is_active=True,
            created_by_run=ga_run,
        )

        run_compound_detection()

        alert = Alert.objects.get(alert_type=Alert.AlertType.COMPOUND_SPIKE)
        for cond in alert.metadata["conditions"]:
            assert "actual_delta_pct" in cond
            assert "threshold_pct" in cond
            assert "metric_key" in cond

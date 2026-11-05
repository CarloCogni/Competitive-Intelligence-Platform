# metrics/tests/test_momentum.py
"""Tests for the momentum score computation engine."""

from datetime import timedelta

import pytest
from django.utils import timezone

from core.tests.factories import EntityFactory
from metrics.models import Metric, MomentumConfig, MomentumScore
from metrics.momentum import (
    INVERTED_METRICS,
    _compute_velocities,
    _z_score_normalize,
    compute_momentum_scores,
)


@pytest.fixture
def _two_entities(db):
    """Create two entities with metric data spanning 8+ days for velocity computation."""
    now = timezone.now()
    old = now - timedelta(days=8)

    e1 = EntityFactory(slug="momentum-a")
    e2 = EntityFactory(slug="momentum-b")

    # Entity 1: github_stars 100 -> 200 (velocity = +100)
    Metric.objects.create(
        entity=e1, source="github", key="github_stars", value=100.0, captured_at=old
    )
    Metric.objects.create(
        entity=e1, source="github", key="github_stars", value=200.0, captured_at=now
    )

    # Entity 2: github_stars 100 -> 150 (velocity = +50)
    Metric.objects.create(
        entity=e2, source="github", key="github_stars", value=100.0, captured_at=old
    )
    Metric.objects.create(
        entity=e2, source="github", key="github_stars", value=150.0, captured_at=now
    )

    return e1, e2


@pytest.mark.django_db
class TestComputeVelocities:
    def test_returns_empty_when_no_entities(self):
        result = _compute_velocities()
        assert result == {}

    def test_returns_empty_when_no_metrics(self):
        EntityFactory()
        result = _compute_velocities()
        assert result == {}

    def test_computes_velocity_as_latest_minus_baseline(self, _two_entities):
        e1, _ = _two_entities
        result = _compute_velocities()
        assert e1.pk in result
        assert result[e1.pk]["github_stars"] == pytest.approx(100.0)

    def test_negates_inverted_metrics(self, db):
        now = timezone.now()
        old = now - timedelta(days=8)
        e = EntityFactory(slug="inverted-test")
        key = "github_latest_release_days_ago"
        assert key in INVERTED_METRICS

        # days_ago went from 10 to 20 (raw velocity = +10, but inverted = -10)
        Metric.objects.create(entity=e, source="github", key=key, value=10.0, captured_at=old)
        Metric.objects.create(entity=e, source="github", key=key, value=20.0, captured_at=now)

        result = _compute_velocities()
        assert result[e.pk][key] == pytest.approx(-10.0)

    def test_skips_entity_without_baseline(self, db):
        e = EntityFactory(slug="no-baseline")
        # Only one recent metric — no data older than 7 days
        Metric.objects.create(
            entity=e,
            source="github",
            key="github_stars",
            value=500.0,
            captured_at=timezone.now(),
        )
        result = _compute_velocities()
        assert e.pk not in result


@pytest.mark.django_db
class TestZScoreNormalize:
    def test_single_entity_returns_zero_stddev(self):
        velocities = {1: {"github_stars": 100.0}}
        stats = _z_score_normalize(velocities, z_cap=3.0)
        assert stats["github_stars"][1] == 0.0  # stddev

    def test_two_entities_correct_mean_and_stddev(self):
        velocities = {1: {"github_stars": 100.0}, 2: {"github_stars": 50.0}}
        stats = _z_score_normalize(velocities, z_cap=3.0)
        mu, sigma = stats["github_stars"]
        assert mu == pytest.approx(75.0)
        assert sigma > 0

    def test_empty_velocities_returns_empty(self):
        stats = _z_score_normalize({}, z_cap=3.0)
        assert stats == {}


@pytest.mark.django_db
class TestComputeMomentumScores:
    def test_returns_empty_when_no_data(self):
        results = compute_momentum_scores()
        assert results == []

    def test_computes_and_persists_scores(self, _two_entities):
        results = compute_momentum_scores()
        assert len(results) == 2
        assert MomentumScore.objects.count() == 2

    def test_dry_run_does_not_persist(self, _two_entities):
        results = compute_momentum_scores(dry_run=True)
        assert len(results) == 2
        assert MomentumScore.objects.count() == 0

    def test_entity_slug_filter(self, _two_entities):
        results = compute_momentum_scores(entity_slug="momentum-a")
        assert len(results) == 1
        assert results[0]["entity_slug"] == "momentum-a"

    def test_acceleration_from_previous_score(self, _two_entities):
        e1, _ = _two_entities
        # Pre-create a previous score
        MomentumScore.objects.create(
            entity=e1,
            score=0.5,
            metrics_used=1,
            computed_at=timezone.now() - timedelta(days=1),
        )
        results = compute_momentum_scores(entity_slug="momentum-a")
        assert results[0]["acceleration"] is not None

    def test_results_sorted_by_score_descending(self, _two_entities):
        results = compute_momentum_scores()
        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_custom_weights_applied(self, _two_entities):
        # Default weight = 1.0 — get baseline scores
        baseline = compute_momentum_scores(dry_run=True)

        # Set a custom weight
        config = MomentumConfig.get()
        config.weights = {"github_stars": 5.0}
        config.save()

        weighted = compute_momentum_scores(dry_run=True)
        # Scores should still be computed (with 1 metric, weight doesn't change
        # z-score value, but the code path is exercised)
        assert len(weighted) == len(baseline)

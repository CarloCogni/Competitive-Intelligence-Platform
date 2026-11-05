# metrics/tests/test_anomaly.py
"""
Unit tests for metrics/anomaly.py — no database required.

All tests use tmp_path for filesystem isolation. The AnomalyDetector and
compute_pct_changes function have zero Django dependencies, so no
@pytest.mark.django_db is needed.
"""

from unittest.mock import MagicMock

import numpy as np
import pytest

from metrics.anomaly import (
    AnomalyDetector,
    AnomalyScore,
    ModelNotFoundError,
    compute_pct_changes,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def model_dir(tmp_path):
    """Isolated model directory for each test."""
    return tmp_path / "anomaly_models"


@pytest.fixture
def detector(model_dir):
    """AnomalyDetector for entity_id=1, github_stars."""
    return AnomalyDetector(model_dir=model_dir, entity_id=1, metric_key="github_stars")


@pytest.fixture
def normal_changes():
    """50 near-zero pct_changes drawn from N(0, 0.02) — typical stable metric."""
    rng = np.random.default_rng(42)
    return rng.normal(loc=0.0, scale=0.02, size=50).tolist()


# ---------------------------------------------------------------------------
# TestComputePctChanges
# ---------------------------------------------------------------------------


class TestComputePctChanges:
    """Tests for the compute_pct_changes helper."""

    def test_basic_increase(self):
        """Single step increase produces correct fractional change."""
        assert compute_pct_changes([100, 110]) == pytest.approx([0.10])

    def test_basic_decrease(self):
        """Single step decrease produces negative change."""
        assert compute_pct_changes([100, 90]) == pytest.approx([-0.10])

    def test_zero_denominator_skipped(self):
        """Pairs with zero in denominator position are dropped."""
        result = compute_pct_changes([0, 100, 110])
        assert result == pytest.approx([0.10])

    def test_empty_input(self):
        """Empty input returns empty list."""
        assert compute_pct_changes([]) == []

    def test_single_value(self):
        """Single value has no pairs, returns empty list."""
        assert compute_pct_changes([100]) == []

    def test_consecutive_chain(self):
        """Chain of values produces correct consecutive changes."""
        result = compute_pct_changes([100, 110, 121])
        assert result == pytest.approx([0.10, 0.10])


# ---------------------------------------------------------------------------
# TestAnomalyDetectorTrain
# ---------------------------------------------------------------------------


class TestAnomalyDetectorTrain:
    """Tests for AnomalyDetector.train()."""

    def test_train_creates_joblib_file(self, detector, normal_changes):
        """Successful training creates a .joblib file on disk."""
        ok = detector.train(normal_changes, min_points=30)
        assert ok is True
        assert detector.model_path.exists()

    def test_train_returns_false_below_min_points(self, detector):
        """Returns False and creates no file when data is insufficient."""
        ok = detector.train([0.01] * 29, min_points=30)
        assert ok is False
        assert not detector.model_path.exists()

    def test_train_returns_true_on_sufficient_data(self, detector):
        """Returns True when exactly min_points samples are provided."""
        ok = detector.train([0.01] * 30, min_points=30)
        assert ok is True

    def test_artifact_p95_is_reasonable(self, detector, normal_changes):
        """p95 from N(0, 0.02) data should be small and positive."""
        detector.train(normal_changes, min_points=30)
        artifact = detector._load_artifact()
        assert 0 < artifact.p95_abs_change < 0.1

    def test_train_overwrites_existing_model(self, detector, normal_changes):
        """Second train call overwrites the existing model file."""
        detector.train(normal_changes, min_points=30)
        mtime1 = detector.model_path.stat().st_mtime

        import time

        time.sleep(0.05)
        detector.train(normal_changes, min_points=30)
        mtime2 = detector.model_path.stat().st_mtime
        assert mtime2 > mtime1

    def test_model_dir_created_on_train(self, tmp_path):
        """Training creates the model directory if it does not exist."""
        nested = tmp_path / "deep" / "nested" / "models"
        det = AnomalyDetector(model_dir=nested, entity_id=1, metric_key="github_stars")
        det.train([0.01] * 30, min_points=30)
        assert nested.exists()

    def test_skips_training_when_reviewer_returns_skip(self, detector, normal_changes):
        """LLM reviewer with verdict='skip' prevents training."""
        from metrics.llm_review import TrainingReview

        reviewer = MagicMock()
        reviewer.review_training_data.return_value = TrainingReview(
            verdict="skip",
            reason="monotonic trend detected",
            recommended_contamination=0.05,
        )
        ok = detector.train(normal_changes, min_points=30, reviewer=reviewer)
        assert ok is False
        assert not detector.model_path.exists()

    def test_uses_reviewer_contamination_when_proceed(self, detector, normal_changes):
        """LLM reviewer's recommended contamination is passed to IsolationForest."""
        from metrics.llm_review import TrainingReview

        reviewer = MagicMock()
        reviewer.review_training_data.return_value = TrainingReview(
            verdict="proceed",
            reason="looks good",
            recommended_contamination=0.10,
        )
        ok = detector.train(normal_changes, min_points=30, reviewer=reviewer)
        assert ok is True
        artifact = detector._load_artifact()
        # The model should have been trained with contamination=0.10
        assert artifact.model.contamination == 0.10

    def test_proceeds_with_warning_on_warn_verdict(self, detector, normal_changes):
        """Reviewer verdict='warn' logs a warning but proceeds with training."""
        from metrics.llm_review import TrainingReview

        reviewer = MagicMock()
        reviewer.review_training_data.return_value = TrainingReview(
            verdict="warn",
            reason="slightly monotonic",
            recommended_contamination=0.05,
        )
        ok = detector.train(normal_changes, min_points=30, reviewer=reviewer)
        assert ok is True
        artifact = detector._load_artifact()
        assert artifact.llm_review_reason == "slightly monotonic"


# ---------------------------------------------------------------------------
# TestAnomalyDetectorScore
# ---------------------------------------------------------------------------


class TestAnomalyDetectorScore:
    """Tests for AnomalyDetector.score()."""

    def test_score_normal_value_not_anomaly(self, detector, normal_changes):
        """A small change against a near-zero trained model is not anomalous."""
        detector.train(normal_changes, min_points=30)
        result = detector.score(0.01)
        assert result.is_anomaly is False

    def test_score_extreme_value_is_anomaly(self, detector, normal_changes):
        """An extreme change is flagged as anomalous."""
        detector.train(normal_changes, min_points=30)
        result = detector.score(10.0)
        assert result.is_anomaly is True

    def test_score_returns_p95(self, detector, normal_changes):
        """score().p95_abs_change matches the trained artifact."""
        detector.train(normal_changes, min_points=30)
        artifact = detector._load_artifact()
        result = detector.score(0.01)
        assert result.p95_abs_change == artifact.p95_abs_change

    def test_score_raises_if_no_model(self, detector):
        """score() raises ModelNotFoundError before any training."""
        with pytest.raises(ModelNotFoundError):
            detector.score(0.01)

    def test_model_exists_false_before_train(self, detector):
        """model_exists() returns False before training."""
        assert detector.model_exists() is False

    def test_model_exists_true_after_train(self, detector, normal_changes):
        """model_exists() returns True after successful training."""
        detector.train(normal_changes, min_points=30)
        assert detector.model_exists() is True

    def test_cold_start_below_threshold_not_anomaly(self, detector):
        """Cold-start artifact: change below threshold is not anomalous."""
        detector.save_cold_start(threshold_pct=0.25, reason="small entity")
        result = detector.score(0.10)
        assert result.is_anomaly is False

    def test_cold_start_above_threshold_is_anomaly(self, detector):
        """Cold-start artifact: change at or above threshold is anomalous."""
        detector.save_cold_start(threshold_pct=0.25, reason="small entity")
        result = detector.score(0.30)
        assert result.is_anomaly is True

    def test_cold_start_p95_is_threshold(self, detector):
        """Cold-start p95_abs_change equals the LLM-suggested threshold."""
        detector.save_cold_start(threshold_pct=0.25, reason="test")
        result = detector.score(0.01)
        assert result.p95_abs_change == 0.25


# ---------------------------------------------------------------------------
# TestAnomalyScore
# ---------------------------------------------------------------------------


class TestAnomalyScore:
    """Tests for the AnomalyScore dataclass."""

    def test_is_frozen_dataclass(self):
        """AnomalyScore is immutable."""
        score = AnomalyScore(is_anomaly=True, p95_abs_change=0.05, raw_score=-0.3)
        with pytest.raises(AttributeError):
            score.is_anomaly = False

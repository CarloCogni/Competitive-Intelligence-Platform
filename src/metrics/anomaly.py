# metrics/anomaly.py
"""
Isolation Forest-based anomaly detector for metric percent-changes.

Design constraints:
  - Trained on consecutive period-over-period pct_changes, NOT raw values.
    Monotonically increasing metrics (e.g. github_stars) would otherwise
    flag all recent readings as anomalous.
  - Requires >= min_points samples (default 30) to fit a full model.
  - Supports cold-start mode: stores an LLM-suggested threshold when
    insufficient data prevents model training.
  - Persists one joblib artifact per (entity_id, metric_key) pair.
  - Artifact bundles the fitted model + p95 absolute pct_change for severity.
  - No Django imports — pure scikit-learn + joblib + pathlib.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest

if TYPE_CHECKING:
    from metrics.llm_review import AnomalyLLMReviewer

logger = logging.getLogger(__name__)

MIN_POINTS_DEFAULT = 30


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnomalyArtifact:
    """
    Persistent artifact saved by AnomalyDetector.train() or save_cold_start().

    Attributes:
        model:            Fitted IsolationForest instance, or None for cold-start.
        p95_abs_change:   95th-percentile absolute pct_change from training data,
                          or LLM-suggested threshold in cold-start mode.
                          Used as effective threshold_pct for _compute_severity().
        metric_key:       Recorded for provenance / debugging.
        entity_id:        Recorded for provenance / debugging.
        trained_on_n:     Number of pct_change samples used during training.
        is_cold_start:    True when model is None and p95 is LLM-suggested.
        llm_review_reason: LLM review notes for transparency / debugging.
    """

    model: IsolationForest | None
    p95_abs_change: float
    metric_key: str
    entity_id: int
    trained_on_n: int
    is_cold_start: bool = field(default=False)
    llm_review_reason: str = field(default="")


@dataclass(frozen=True)
class AnomalyScore:
    """Result of AnomalyDetector.score()."""

    is_anomaly: bool
    p95_abs_change: float
    raw_score: float


class ModelNotFoundError(FileNotFoundError):
    """Raised when score() is called with no trained model on disk."""


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def compute_pct_changes(values: Sequence[float]) -> list[float]:
    """
    Compute consecutive period-over-period fractional changes.

    Args:
        values: Metric values ordered oldest-first. Zero-values in the
                denominator position are skipped (pair dropped entirely).

    Returns:
        List of fractional changes, e.g. [100, 110, 105] -> [0.10, -0.0454...].
    """
    changes: list[float] = []
    for prev, curr in zip(values, values[1:]):
        if prev == 0:
            continue
        changes.append((curr - prev) / prev)
    return changes


# ---------------------------------------------------------------------------
# AnomalyDetector
# ---------------------------------------------------------------------------


class AnomalyDetector:
    """
    Manages training, persistence, and scoring of Isolation Forest models
    for one (entity_id, metric_key) pair.

    Usage (training):
        detector = AnomalyDetector(model_dir, entity_id=1, metric_key="github_stars")
        ok = detector.train(pct_changes, min_points=30)

    Usage (scoring):
        result = detector.score(current_pct_change)
        if result.is_anomaly:
            ...  # use result.p95_abs_change as effective_threshold_pct
    """

    def __init__(self, model_dir: Path, entity_id: int, metric_key: str) -> None:
        """
        Args:
            model_dir:  Directory where .joblib files are stored.
            entity_id:  FK to Entity. Used in the filename.
            metric_key: e.g. "github_stars". Used in the filename.
        """
        self.model_dir = model_dir
        self.entity_id = entity_id
        self.metric_key = metric_key

    @property
    def model_path(self) -> Path:
        """Absolute path of the .joblib artifact for this (entity_id, metric_key)."""
        safe_key = self.metric_key.replace("/", "_")
        return self.model_dir / f"{self.entity_id}_{safe_key}.joblib"

    def model_exists(self) -> bool:
        """Return True if the .joblib artifact exists on disk."""
        return self.model_path.exists()

    def train(
        self,
        pct_changes: Sequence[float],
        min_points: int = MIN_POINTS_DEFAULT,
        contamination: float = 0.05,
        random_state: int = 42,
        reviewer: AnomalyLLMReviewer | None = None,
        entity_slug: str = "",
        span_days: int = 0,
    ) -> bool:
        """
        Fit an IsolationForest on the supplied pct_changes and save the artifact.

        Args:
            pct_changes:   Consecutive period-over-period fractional changes.
            min_points:    Minimum samples required to fit. Returns False if fewer.
            contamination: Expected fraction of outliers (IsolationForest param).
            random_state:  Seed for reproducibility.
            reviewer:      Optional LLM reviewer for data quality gating.
            entity_slug:   Entity slug for LLM context (only used if reviewer set).
            span_days:     Calendar span of the data (only used if reviewer set).

        Returns:
            True if model was trained and saved; False if insufficient data
            or LLM reviewer vetoed the training.
        """
        if len(pct_changes) < min_points:
            logger.warning(
                "Insufficient data for entity_id=%d key=%s: need %d, got %d. Skipping.",
                self.entity_id,
                self.metric_key,
                min_points,
                len(pct_changes),
            )
            return False

        X = np.array(pct_changes, dtype=float).reshape(-1, 1)

        # LLM review gate
        llm_reason = ""
        if reviewer is not None:
            abs_changes = np.abs(X.ravel())
            positive_frac = float(np.mean(X.ravel() > 0))
            negative_frac = float(np.mean(X.ravel() < 0))
            pct_monotonic = max(positive_frac, negative_frac)

            review = reviewer.review_training_data(
                entity_slug=entity_slug,
                metric_key=self.metric_key,
                n=len(pct_changes),
                mean_change=float(np.mean(X)),
                std_change=float(np.std(X)),
                p5=float(np.percentile(X, 5)),
                p95=float(np.percentile(X, 95)),
                span_days=span_days,
                pct_monotonic=pct_monotonic,
            )
            llm_reason = review.reason

            if review.verdict == "skip":
                logger.info(
                    "LLM review vetoed training for entity_id=%d key=%s: %s",
                    self.entity_id,
                    self.metric_key,
                    review.reason,
                )
                return False

            if review.verdict == "warn":
                logger.warning(
                    "LLM review warning for entity_id=%d key=%s: %s (proceeding).",
                    self.entity_id,
                    self.metric_key,
                    review.reason,
                )

            # Use LLM-recommended contamination
            contamination = review.recommended_contamination

        # Fit Isolation Forest
        p95 = max(float(np.percentile(abs_changes if reviewer else np.abs(X.ravel()), 95)), 1e-6)

        iso = IsolationForest(contamination=contamination, random_state=random_state)
        iso.fit(X)

        artifact = AnomalyArtifact(
            model=iso,
            p95_abs_change=p95,
            metric_key=self.metric_key,
            entity_id=self.entity_id,
            trained_on_n=len(pct_changes),
            is_cold_start=False,
            llm_review_reason=llm_reason,
        )

        self.model_dir.mkdir(parents=True, exist_ok=True)
        joblib.dump(artifact, self.model_path)
        logger.info(
            "Trained model: %s (n=%d, p95=%.4f)",
            self.model_path.name,
            len(pct_changes),
            p95,
        )
        return True

    def save_cold_start(self, threshold_pct: float, reason: str) -> None:
        """
        Save a cold-start artifact with an LLM-suggested threshold.

        Used when an entity has insufficient data for model training but the
        LLM can suggest a reasonable threshold based on entity characteristics.

        Args:
            threshold_pct: LLM-suggested fractional threshold (e.g. 0.25).
            reason: LLM explanation for the suggestion.
        """
        artifact = AnomalyArtifact(
            model=None,
            p95_abs_change=threshold_pct,
            metric_key=self.metric_key,
            entity_id=self.entity_id,
            trained_on_n=0,
            is_cold_start=True,
            llm_review_reason=reason,
        )
        self.model_dir.mkdir(parents=True, exist_ok=True)
        joblib.dump(artifact, self.model_path)
        logger.info(
            "Saved cold-start artifact: %s (threshold=%.4f)",
            self.model_path.name,
            threshold_pct,
        )

    def score(self, pct_change: float) -> AnomalyScore:
        """
        Score a single pct_change against the saved model.

        For cold-start artifacts, compares abs(pct_change) against the
        LLM-suggested threshold directly. For full IF models, calls
        model.predict() and model.score_samples().

        Args:
            pct_change: The fractional change to evaluate (e.g. 0.30 for 30%).

        Returns:
            AnomalyScore with is_anomaly, p95_abs_change, and raw_score.

        Raises:
            ModelNotFoundError: If no artifact exists for this pair.
        """
        artifact = self._load_artifact()

        if artifact.is_cold_start:
            # Cold-start: direct threshold comparison
            is_anomaly = abs(pct_change) >= artifact.p95_abs_change
            return AnomalyScore(
                is_anomaly=is_anomaly,
                p95_abs_change=artifact.p95_abs_change,
                raw_score=pct_change,
            )

        # Full Isolation Forest model
        X = np.array([[pct_change]], dtype=float)
        raw = float(artifact.model.score_samples(X)[0])
        is_anomaly = bool(artifact.model.predict(X)[0] == -1)
        return AnomalyScore(
            is_anomaly=is_anomaly,
            p95_abs_change=artifact.p95_abs_change,
            raw_score=raw,
        )

    def _load_artifact(self) -> AnomalyArtifact:
        """Load the saved AnomalyArtifact from disk."""
        if not self.model_path.exists():
            raise ModelNotFoundError(
                f"No anomaly model at {self.model_path} for "
                f"entity_id={self.entity_id} key={self.metric_key}"
            )
        return joblib.load(self.model_path)

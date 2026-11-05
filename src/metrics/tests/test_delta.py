# metrics/tests/test_delta.py
"""Tests for delta detection logic in metrics/delta.py."""

import numpy as np
import pytest
from django.utils import timezone

from core.tests.factories import EntityFactory
from metrics.anomaly import AnomalyDetector
from metrics.delta import (
    _compute_severity,
    run_changelog_release_detection,
    run_delta_detection,
    run_release_detection,
)
from metrics.models import Alert, MetricSnapshot
from metrics.tests.factories import MetricFactory

# Mirrors DeltaThreshold.lookback_days seeded in conftest.py
LOOKBACK_DAYS = 7


def _make_github_snapshot(entity, data: dict, captured_at=None):
    """Helper: create a MetricSnapshot with github source."""
    kwargs = {"entity": entity, "source": "github", "data": data}
    if captured_at:
        kwargs["captured_at"] = captured_at
    return MetricSnapshot.objects.create(**kwargs)


@pytest.mark.django_db
class TestRunReleaseDetection:
    """Tests for run_release_detection() — NEW_RELEASE and NEW_REPO alert logic."""

    def test_new_release_fires_alert(self):
        """Different release tags between snapshots → NEW_RELEASE alert."""
        entity = EntityFactory()
        _make_github_snapshot(
            entity,
            {"latest_release_tag": "v1.0.0", "latest_release_published_at": "2024-01-01T00:00:00Z"},
            captured_at=_days_ago(2),
        )
        _make_github_snapshot(
            entity,
            {"latest_release_tag": "v2.0.0", "latest_release_published_at": "2024-06-01T00:00:00Z"},
        )

        count = run_release_detection()

        assert count == 1
        alert = Alert.objects.get(entity=entity, alert_type=Alert.AlertType.NEW_RELEASE)
        assert alert.metadata["release_tag"] == "v2.0.0"
        assert alert.metadata["previous_tag"] == "v1.0.0"

    def test_no_alert_on_same_release_tag(self):
        """Same release tag in both snapshots → no alert."""
        entity = EntityFactory()
        _make_github_snapshot(
            entity,
            {"latest_release_tag": "v1.0.0", "latest_release_published_at": "2024-01-01T00:00:00Z"},
            captured_at=_days_ago(2),
        )
        _make_github_snapshot(
            entity,
            {"latest_release_tag": "v1.0.0", "latest_release_published_at": "2024-01-01T00:00:00Z"},
        )

        count = run_release_detection()

        assert count == 0
        assert Alert.objects.filter(entity=entity).count() == 0

    def test_new_repo_fires_alert(self):
        """public_repos increased between snapshots → NEW_REPO alert."""
        entity = EntityFactory()
        _make_github_snapshot(entity, {"public_repos": 5}, captured_at=_days_ago(2))
        _make_github_snapshot(entity, {"public_repos": 6})

        count = run_release_detection()

        assert count == 1
        alert = Alert.objects.get(entity=entity, alert_type=Alert.AlertType.NEW_REPO)
        assert alert.metadata["previous_count"] == 5
        assert alert.metadata["new_count"] == 6

    def test_no_new_repo_alert_on_same_count(self):
        """Same public_repos count → no alert."""
        entity = EntityFactory()
        _make_github_snapshot(entity, {"public_repos": 5}, captured_at=_days_ago(2))
        _make_github_snapshot(entity, {"public_repos": 5})

        count = run_release_detection()

        assert count == 0

    def test_dedup_does_not_fire_twice(self):
        """Calling run_release_detection() twice with same data creates only 1 alert."""
        entity = EntityFactory()
        _make_github_snapshot(
            entity,
            {"latest_release_tag": "v1.0.0"},
            captured_at=_days_ago(2),
        )
        _make_github_snapshot(entity, {"latest_release_tag": "v2.0.0"})

        run_release_detection()
        run_release_detection()

        assert (
            Alert.objects.filter(entity=entity, alert_type=Alert.AlertType.NEW_RELEASE).count() == 1
        )

    def test_single_snapshot_does_not_fire(self):
        """Only one snapshot — nothing to compare against → no alert."""
        entity = EntityFactory()
        _make_github_snapshot(entity, {"latest_release_tag": "v1.0.0", "public_repos": 3})

        count = run_release_detection()

        assert count == 0
        assert Alert.objects.filter(entity=entity).count() == 0

    def test_both_new_release_and_new_repo_fire(self):
        """When both conditions are met, two alerts are created in one run."""
        entity = EntityFactory()
        _make_github_snapshot(
            entity,
            {"latest_release_tag": "v1.0.0", "public_repos": 5},
            captured_at=_days_ago(2),
        )
        _make_github_snapshot(entity, {"latest_release_tag": "v2.0.0", "public_repos": 7})

        count = run_release_detection()

        assert count == 2
        assert (
            Alert.objects.filter(entity=entity, alert_type=Alert.AlertType.NEW_RELEASE).count() == 1
        )
        assert Alert.objects.filter(entity=entity, alert_type=Alert.AlertType.NEW_REPO).count() == 1


def _days_ago(n: int):
    """Return a timezone-aware datetime n days in the past."""
    return timezone.now() - timezone.timedelta(days=n)


@pytest.mark.django_db
class TestRunDeltaDetection:
    def test_spike_creates_alert(self):
        """A >20% increase in github_stars triggers a METRIC_SPIKE alert."""
        entity = EntityFactory()
        # Baseline: 7 days ago
        MetricFactory(
            entity=entity,
            source="github",
            key="github_stars",
            value=1000.0,
            captured_at=_days_ago(LOOKBACK_DAYS + 1),
        )
        # Latest: now (50% increase)
        MetricFactory(
            entity=entity,
            source="github",
            key="github_stars",
            value=1500.0,
            captured_at=timezone.now(),
        )

        run_delta_detection("github")

        alert = Alert.objects.filter(entity=entity, alert_type=Alert.AlertType.METRIC_SPIKE).first()
        assert alert is not None
        assert "github_stars" in alert.message

    def test_no_alert_below_threshold(self):
        """A 5% change in github_stars (below 20% threshold) must not create an alert."""
        entity = EntityFactory()
        MetricFactory(
            entity=entity,
            source="github",
            key="github_stars",
            value=1000.0,
            captured_at=_days_ago(LOOKBACK_DAYS + 1),
        )
        MetricFactory(
            entity=entity,
            source="github",
            key="github_stars",
            value=1050.0,
            captured_at=timezone.now(),
        )

        run_delta_detection("github")

        assert Alert.objects.filter(entity=entity).count() == 0

    def test_single_reading_skipped(self):
        """Only one reading (no baseline) → no alert."""
        entity = EntityFactory()
        MetricFactory(entity=entity, source="github", key="github_stars", value=1000.0)

        run_delta_detection("github")

        assert Alert.objects.filter(entity=entity).count() == 0

    def test_zero_baseline_skipped(self):
        """Baseline of zero → division by zero is avoided, no alert created."""
        entity = EntityFactory()
        MetricFactory(
            entity=entity,
            source="github",
            key="github_stars",
            value=0.0,
            captured_at=_days_ago(LOOKBACK_DAYS + 1),
        )
        MetricFactory(
            entity=entity,
            source="github",
            key="github_stars",
            value=100.0,
            captured_at=timezone.now(),
        )

        run_delta_detection("github")

        assert Alert.objects.filter(entity=entity).count() == 0

    def test_decline_also_triggers_alert(self):
        """A large decrease (>20%) also creates an alert (abs value check)."""
        entity = EntityFactory()
        MetricFactory(
            entity=entity,
            source="github",
            key="github_stars",
            value=1000.0,
            captured_at=_days_ago(LOOKBACK_DAYS + 1),
        )
        MetricFactory(
            entity=entity,
            source="github",
            key="github_stars",
            value=700.0,
            captured_at=timezone.now(),
        )

        run_delta_detection("github")

        assert Alert.objects.filter(entity=entity).count() == 1

    def test_unknown_metric_key_skipped(self):
        """Metric keys without a configured threshold are ignored."""
        entity = EntityFactory()
        MetricFactory(
            entity=entity,
            source="github",
            key="github_unknown_metric",
            value=1.0,
            captured_at=_days_ago(LOOKBACK_DAYS + 1),
        )
        MetricFactory(
            entity=entity,
            source="github",
            key="github_unknown_metric",
            value=9999.0,
            captured_at=timezone.now(),
        )

        run_delta_detection("github")

        assert Alert.objects.filter(entity=entity).count() == 0

    def test_alert_metadata_contains_values(self):
        """Alert metadata must include old_value, new_value, delta_pct, and severity."""
        entity = EntityFactory()
        # 100% increase → ratio = 1.0 / 0.20 = 5.0 → HIGH
        MetricFactory(
            entity=entity,
            source="github",
            key="github_stars",
            value=1000.0,
            captured_at=_days_ago(LOOKBACK_DAYS + 1),
        )
        MetricFactory(
            entity=entity,
            source="github",
            key="github_stars",
            value=2000.0,
            captured_at=timezone.now(),
        )

        run_delta_detection("github")

        alert = Alert.objects.get(entity=entity)
        assert alert.metadata["old_value"] == 1000.0
        assert alert.metadata["new_value"] == 2000.0
        assert alert.metadata["delta_pct"] == 100.0
        assert alert.metadata["severity"] == "HIGH"

    def test_returns_alert_count(self):
        """run_delta_detection returns the number of alerts created."""
        entity = EntityFactory()
        MetricFactory(
            entity=entity,
            source="github",
            key="github_stars",
            value=1000.0,
            captured_at=_days_ago(LOOKBACK_DAYS + 1),
        )
        MetricFactory(
            entity=entity,
            source="github",
            key="github_stars",
            value=1500.0,
            captured_at=timezone.now(),
        )

        count = run_delta_detection("github")
        assert count == 1


class TestComputeSeverity:
    """Unit tests for _compute_severity — no DB needed."""

    def test_low_severity(self):
        """Delta just above threshold → LOW."""
        # 1.1× threshold
        assert _compute_severity(0.22, 0.20) == "LOW"

    def test_medium_severity(self):
        """Delta 3× threshold → MEDIUM."""
        assert _compute_severity(0.60, 0.20) == "MEDIUM"

    def test_high_severity(self):
        """Delta 5× or more threshold → HIGH."""
        assert _compute_severity(1.00, 0.20) == "HIGH"

    def test_boundary_2x_is_medium(self):
        """Exactly 2× threshold is MEDIUM, not LOW."""
        assert _compute_severity(0.40, 0.20) == "MEDIUM"

    def test_boundary_5x_is_high(self):
        """Exactly 5× threshold is HIGH, not MEDIUM."""
        assert _compute_severity(1.00, 0.20) == "HIGH"


@pytest.mark.django_db
class TestRunDeltaDetectionDynamic:
    """
    Integration tests for the dynamic (Isolation Forest / cold-start) path
    in run_delta_detection().

    Strategy: override settings.ANOMALY_MODEL_DIR to tmp_path for filesystem
    isolation. Pre-train a real model before testing the detection path.
    """

    def test_dynamic_anomaly_fires_alert(self, tmp_path, settings, dynamic_threshold):
        """An extreme spike triggers an alert via the dynamic path."""
        settings.ANOMALY_MODEL_DIR = tmp_path

        entity = EntityFactory()

        # Train a model on near-zero changes
        rng = np.random.default_rng(42)
        normal_changes = rng.normal(0.0, 0.02, size=50).tolist()
        detector = AnomalyDetector(tmp_path, entity_id=entity.pk, metric_key="github_stars")
        detector.train(normal_changes, min_points=30)

        # Create baseline and spiked latest metric
        MetricFactory(
            entity=entity,
            source="github",
            key="github_stars",
            value=1000.0,
            captured_at=_days_ago(LOOKBACK_DAYS + 1),
        )
        MetricFactory(
            entity=entity,
            source="github",
            key="github_stars",
            value=5000.0,
            captured_at=timezone.now(),
        )

        count = run_delta_detection("github")

        assert count == 1
        alert = Alert.objects.get(entity=entity, alert_type=Alert.AlertType.METRIC_SPIKE)
        assert alert is not None

    def test_dynamic_normal_value_no_alert(self, tmp_path, settings, dynamic_threshold):
        """A small change does not fire an alert in dynamic mode."""
        settings.ANOMALY_MODEL_DIR = tmp_path

        entity = EntityFactory()

        # Train on near-zero changes
        rng = np.random.default_rng(42)
        normal_changes = rng.normal(0.0, 0.02, size=50).tolist()
        detector = AnomalyDetector(tmp_path, entity_id=entity.pk, metric_key="github_stars")
        detector.train(normal_changes, min_points=30)

        # Tiny change: 1000 → 1010 = 1%
        MetricFactory(
            entity=entity,
            source="github",
            key="github_stars",
            value=1000.0,
            captured_at=_days_ago(LOOKBACK_DAYS + 1),
        )
        MetricFactory(
            entity=entity,
            source="github",
            key="github_stars",
            value=1010.0,
            captured_at=timezone.now(),
        )

        count = run_delta_detection("github")

        assert count == 0
        assert Alert.objects.filter(entity=entity).count() == 0

    def test_dynamic_falls_back_to_static_when_no_model(
        self, tmp_path, settings, dynamic_threshold
    ):
        """No model file → falls back to static threshold, fires if above it."""
        settings.ANOMALY_MODEL_DIR = tmp_path
        # Do NOT train a model — model file is absent

        entity = EntityFactory()
        MetricFactory(
            entity=entity,
            source="github",
            key="github_stars",
            value=1000.0,
            captured_at=_days_ago(LOOKBACK_DAYS + 1),
        )
        # 50% increase — above static threshold of 20%
        MetricFactory(
            entity=entity,
            source="github",
            key="github_stars",
            value=1500.0,
            captured_at=timezone.now(),
        )

        count = run_delta_detection("github")

        assert count == 1
        alert = Alert.objects.get(entity=entity)
        # Static threshold should be used (20% = 20.0 in metadata)
        assert alert.metadata["threshold_pct"] == 20.0

    def test_static_path_unchanged_when_flag_false(self, settings):
        """use_dynamic_threshold=False uses static path (no model dir interaction)."""
        # Don't set ANOMALY_MODEL_DIR — static path shouldn't touch it
        entity = EntityFactory()
        MetricFactory(
            entity=entity,
            source="github",
            key="github_stars",
            value=1000.0,
            captured_at=_days_ago(LOOKBACK_DAYS + 1),
        )
        MetricFactory(
            entity=entity,
            source="github",
            key="github_stars",
            value=1500.0,
            captured_at=timezone.now(),
        )

        count = run_delta_detection("github")

        assert count == 1
        alert = Alert.objects.get(entity=entity)
        assert alert.metadata["threshold_pct"] == 20.0

    def test_dynamic_alert_metadata_uses_p95(self, tmp_path, settings, dynamic_threshold):
        """Dynamic alert metadata['threshold_pct'] reflects p95, not static threshold."""
        settings.ANOMALY_MODEL_DIR = tmp_path

        entity = EntityFactory()

        rng = np.random.default_rng(42)
        normal_changes = rng.normal(0.0, 0.02, size=50).tolist()
        detector = AnomalyDetector(tmp_path, entity_id=entity.pk, metric_key="github_stars")
        detector.train(normal_changes, min_points=30)
        artifact = detector._load_artifact()

        MetricFactory(
            entity=entity,
            source="github",
            key="github_stars",
            value=1000.0,
            captured_at=_days_ago(LOOKBACK_DAYS + 1),
        )
        MetricFactory(
            entity=entity,
            source="github",
            key="github_stars",
            value=5000.0,
            captured_at=timezone.now(),
        )

        run_delta_detection("github")

        alert = Alert.objects.get(entity=entity)
        # threshold_pct should be p95 * 100 (rounded to 2 decimal places)
        expected = round(artifact.p95_abs_change * 100, 2)
        assert alert.metadata["threshold_pct"] == expected

    def test_cold_start_artifact_fires_alert(self, tmp_path, settings, dynamic_threshold):
        """Cold-start artifact with LLM-suggested threshold fires on large changes."""
        settings.ANOMALY_MODEL_DIR = tmp_path

        entity = EntityFactory()

        # Save a cold-start artifact with 15% threshold
        detector = AnomalyDetector(tmp_path, entity_id=entity.pk, metric_key="github_stars")
        detector.save_cold_start(threshold_pct=0.15, reason="small entity")

        MetricFactory(
            entity=entity,
            source="github",
            key="github_stars",
            value=1000.0,
            captured_at=_days_ago(LOOKBACK_DAYS + 1),
        )
        # 50% increase — well above the 15% cold-start threshold
        MetricFactory(
            entity=entity,
            source="github",
            key="github_stars",
            value=1500.0,
            captured_at=timezone.now(),
        )

        count = run_delta_detection("github")

        assert count == 1
        alert = Alert.objects.get(entity=entity)
        assert alert.metadata["threshold_pct"] == 15.0


# ---------------------------------------------------------------------------
# Changelog release detection
# ---------------------------------------------------------------------------


def _make_changelog_snapshot(entity, data: dict, captured_at=None):
    """Helper: create a MetricSnapshot with changelog source."""
    kwargs = {"entity": entity, "source": "changelog", "data": data}
    if captured_at:
        kwargs["captured_at"] = captured_at
    return MetricSnapshot.objects.create(**kwargs)


@pytest.mark.django_db
class TestRunChangelogReleaseDetection:
    """Tests for run_changelog_release_detection() — NEW_RELEASE from changelog source."""

    def test_new_tag_fires_alert(self):
        entity = EntityFactory()
        _make_changelog_snapshot(
            entity,
            {"latest_tag": "v1.0.0"},
            captured_at=_days_ago(1),
        )
        _make_changelog_snapshot(
            entity,
            {"latest_tag": "v2.0.0", "latest_url": "https://example.com/v2"},
        )

        count = run_changelog_release_detection()

        assert count == 1
        alert = Alert.objects.get(entity=entity)
        assert alert.alert_type == Alert.AlertType.NEW_RELEASE
        assert alert.metadata["release_tag"] == "v2.0.0"
        assert alert.metadata["source"] == "changelog"

    def test_same_tag_no_alert(self):
        entity = EntityFactory()
        _make_changelog_snapshot(entity, {"latest_tag": "v1.0.0"}, captured_at=_days_ago(1))
        _make_changelog_snapshot(entity, {"latest_tag": "v1.0.0"})

        count = run_changelog_release_detection()
        assert count == 0

    def test_single_snapshot_no_alert(self):
        entity = EntityFactory()
        _make_changelog_snapshot(entity, {"latest_tag": "v1.0.0"})

        count = run_changelog_release_detection()
        assert count == 0

    def test_deduplication(self):
        entity = EntityFactory()
        _make_changelog_snapshot(entity, {"latest_tag": "v1.0.0"}, captured_at=_days_ago(1))
        _make_changelog_snapshot(entity, {"latest_tag": "v2.0.0"})

        run_changelog_release_detection()
        count = run_changelog_release_detection()  # second run should not duplicate
        assert count == 0
        assert Alert.objects.filter(entity=entity).count() == 1

    def test_empty_tag_no_alert(self):
        entity = EntityFactory()
        _make_changelog_snapshot(entity, {"latest_tag": "v1.0.0"}, captured_at=_days_ago(1))
        _make_changelog_snapshot(entity, {"latest_tag": ""})

        count = run_changelog_release_detection()
        assert count == 0

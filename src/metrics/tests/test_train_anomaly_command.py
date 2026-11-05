# metrics/tests/test_train_anomaly_command.py
"""Tests for the train_anomaly_models management command."""

import pytest
from django.core.management import call_command

from metrics.models import DeltaThreshold


@pytest.mark.django_db
class TestTrainAnomalyModelsCommand:
    """Tests for train_anomaly_models management command."""

    def test_no_dynamic_thresholds_exits_early(self, capsys):
        """When no DeltaThreshold has use_dynamic_threshold=True, exits with warning."""
        # Ensure none are dynamic (seed_thresholds conftest sets default=False)
        DeltaThreshold.objects.update(use_dynamic_threshold=False)
        call_command("train_anomaly_models", "--skip-llm-review")
        captured = capsys.readouterr()
        assert "No DeltaThreshold" in captured.out

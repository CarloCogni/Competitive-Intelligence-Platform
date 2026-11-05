# metrics/tests/test_optimize_command.py
"""Tests for the optimize_alert_rules management command."""

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


@pytest.mark.django_db
class TestOptimizeAlertRulesCommand:
    """Tests for optimize_alert_rules management command."""

    def test_no_data_raises_command_error(self):
        """Command raises CommandError when there are no historical events."""
        with pytest.raises(CommandError, match="No historical events"):
            call_command("optimize_alert_rules")

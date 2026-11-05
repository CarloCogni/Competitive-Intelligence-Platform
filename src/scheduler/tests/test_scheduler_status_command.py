# scheduler/tests/test_scheduler_status_command.py
"""Tests for the scheduler_status management command."""

import pytest
from django.core.management import call_command


@pytest.mark.django_db
class TestSchedulerStatusCommand:
    """Tests for scheduler_status management command."""

    def test_no_jobs_prints_warning(self, capsys):
        call_command("scheduler_status")
        captured = capsys.readouterr()
        assert "No scheduled jobs" in captured.out

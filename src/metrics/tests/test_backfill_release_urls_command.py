# metrics/tests/test_backfill_release_urls_command.py
"""Tests for the backfill_release_urls management command."""

import pytest
from django.core.management import call_command

from core.tests.factories import EntityFactory
from metrics.models import Alert


@pytest.mark.django_db
class TestBackfillReleaseUrlsCommand:
    def test_backfills_missing_url(self):
        e = EntityFactory(github_org="blockstream", github_repo="green_android")
        alert = Alert.objects.create(
            entity=e,
            alert_type=Alert.AlertType.NEW_RELEASE,
            message="New release v1.0",
            metadata={"release_tag": "v1.0"},
        )
        call_command("backfill_release_urls")
        alert.refresh_from_db()
        assert alert.metadata["release_url"] == (
            "https://github.com/blockstream/green_android/releases/tag/v1.0"
        )

    def test_skips_alert_with_existing_url(self):
        e = EntityFactory(github_org="blockstream", github_repo="green_android")
        existing_url = "https://github.com/blockstream/green_android/releases/tag/v0.9"
        alert = Alert.objects.create(
            entity=e,
            alert_type=Alert.AlertType.NEW_RELEASE,
            message="New release v1.0",
            metadata={"release_tag": "v1.0", "release_url": existing_url},
        )
        call_command("backfill_release_urls")
        alert.refresh_from_db()
        assert alert.metadata["release_url"] == existing_url

    def test_skips_alert_missing_tag_or_org(self, capsys):
        e = EntityFactory(github_org="", github_repo="")
        Alert.objects.create(
            entity=e,
            alert_type=Alert.AlertType.NEW_RELEASE,
            message="New release",
            metadata={"release_tag": "v1.0"},
        )
        call_command("backfill_release_urls")
        captured = capsys.readouterr()
        assert "skipped 1" in captured.out.lower()

    def test_dry_run_does_not_save(self, capsys):
        e = EntityFactory(github_org="blockstream", github_repo="green_android")
        alert = Alert.objects.create(
            entity=e,
            alert_type=Alert.AlertType.NEW_RELEASE,
            message="New release v1.0",
            metadata={"release_tag": "v1.0"},
        )
        call_command("backfill_release_urls", "--dry-run")
        alert.refresh_from_db()
        assert "release_url" not in alert.metadata
        captured = capsys.readouterr()
        assert "Would update" in captured.out

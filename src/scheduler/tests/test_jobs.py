# scheduler/tests/test_jobs.py
"""Tests for scheduler job functions and batch_run_context."""

from unittest.mock import MagicMock, patch

import pytest

from core.tests.factories import EntityFactory
from scheduler.jobs import batch_run_context
from scheduler.models import BatchRun


@pytest.mark.django_db
class TestBatchRunContext:
    """Tests for batch_run_context — context manager wrapping job execution."""

    def test_success_path(self):
        with batch_run_context("test_job") as run:
            run.entities_total = 2
            run.entities_success = 2
            run.entities_failed = 0

        run.refresh_from_db()
        assert run.status == BatchRun.Status.SUCCESS
        assert run.finished_at is not None

    def test_partial_path(self):
        with batch_run_context("test_job") as run:
            run.entities_total = 3
            run.entities_success = 2
            run.entities_failed = 1

        run.refresh_from_db()
        assert run.status == BatchRun.Status.PARTIAL

    def test_all_failed_path(self):
        with batch_run_context("test_job") as run:
            run.entities_total = 2
            run.entities_success = 0
            run.entities_failed = 2

        run.refresh_from_db()
        assert run.status == BatchRun.Status.FAILED

    def test_exception_path(self):
        with pytest.raises(RuntimeError, match="boom"):
            with batch_run_context("test_job") as run:
                raise RuntimeError("boom")

        run.refresh_from_db()
        assert run.status == BatchRun.Status.FAILED
        assert run.finished_at is not None


@pytest.mark.django_db
class TestRunFetcherJob:
    """Tests for _run_fetcher_job — generic fetcher runner."""

    def test_happy_path(self):
        entity = EntityFactory(github_org="org", github_repo="repo")
        mock_fetcher_instance = MagicMock()
        mock_fetcher_instance.fetch.return_value = {"stars": 100}
        mock_fetcher_class = MagicMock(return_value=mock_fetcher_instance)

        with patch("importlib.import_module") as mock_import:
            mock_module = MagicMock()
            mock_module.FakeFetcher = mock_fetcher_class
            mock_import.return_value = mock_module

            from scheduler.jobs import _run_fetcher_job

            _run_fetcher_job(
                source="test",
                display_name="Test",
                fetcher_class_path="fake.module.FakeFetcher",
                entity_queryset=[entity],
            )

        mock_fetcher_instance.fetch.assert_called_once()
        mock_fetcher_instance.store.assert_called_once()
        assert BatchRun.objects.filter(job_id="test_fetcher").exists()

    def test_entity_failure_records_error(self):
        entity = EntityFactory(github_org="org")
        mock_fetcher_instance = MagicMock()
        mock_fetcher_instance.fetch.side_effect = Exception("API down")
        mock_fetcher_class = MagicMock(return_value=mock_fetcher_instance)

        with patch("importlib.import_module") as mock_import:
            mock_module = MagicMock()
            mock_module.FailFetcher = mock_fetcher_class
            mock_import.return_value = mock_module

            from scheduler.jobs import _run_fetcher_job

            _run_fetcher_job(
                source="test",
                display_name="Test",
                fetcher_class_path="fake.module.FailFetcher",
                entity_queryset=[entity],
            )

        run = BatchRun.objects.filter(job_id="test_fetcher").first()
        assert run.entities_failed == 1
        assert entity.slug in run.error_log

    def test_post_fetch_hooks_called(self):
        hook = MagicMock()
        mock_fetcher_instance = MagicMock()
        mock_fetcher_instance.fetch.return_value = {"data": 1}
        mock_fetcher_class = MagicMock(return_value=mock_fetcher_instance)

        with patch("importlib.import_module") as mock_import:
            mock_module = MagicMock()
            mock_module.HookFetcher = mock_fetcher_class
            mock_import.return_value = mock_module

            from scheduler.jobs import _run_fetcher_job

            _run_fetcher_job(
                source="hooktest",
                display_name="Hook Test",
                fetcher_class_path="fake.module.HookFetcher",
                entity_queryset=[EntityFactory()],
                post_fetch_hooks=[hook],
            )

        hook.assert_called_once()


@pytest.mark.django_db
class TestRescheduleJob:
    """Tests for reschedule_job — live rescheduling from FetcherConfig."""

    def test_scheduler_not_started_is_noop(self):
        from scheduler.jobs import reschedule_job

        with patch("scheduler.jobs._scheduler", None):
            reschedule_job("github")  # should not raise

    def test_unknown_source_logs_warning(self):
        from scheduler.jobs import reschedule_job

        mock_sched = MagicMock()
        mock_sched.running = True
        with patch("scheduler.jobs._scheduler", mock_sched):
            reschedule_job("nonexistent_source")  # should not raise


@pytest.mark.django_db
class TestMonthlyJobs:
    """Tests for monthly maintenance jobs that delegate to management commands."""

    @patch("django.core.management.call_command")
    def test_monthly_anomaly_training_calls_command(self, mock_cmd):
        from scheduler.jobs import monthly_anomaly_training_job

        monthly_anomaly_training_job()
        mock_cmd.assert_called_once_with("train_anomaly_models")

    @patch("django.core.management.call_command")
    def test_monthly_compound_optimization_calls_command(self, mock_cmd):
        from scheduler.jobs import monthly_compound_optimization_job

        monthly_compound_optimization_job()
        mock_cmd.assert_called_once_with("optimize_alert_rules")


@pytest.mark.django_db
class TestWeeklyBriefingJob:
    """Tests for weekly_briefing_job."""

    @patch("briefings.generator.BriefingGenerator")
    def test_creates_briefing(self, mock_gen_cls):
        from scheduler.jobs import weekly_briefing_job

        mock_instance = MagicMock()
        mock_briefing = MagicMock(pk=1, status="ready")
        mock_instance.run.return_value = mock_briefing
        mock_gen_cls.return_value = mock_instance

        weekly_briefing_job()
        mock_instance.run.assert_called_once()


@pytest.mark.django_db
class TestJobFunctionWrappers:
    """Tests for individual run_*_fetcher job functions — verify they call _run_fetcher_job."""

    @patch("scheduler.jobs._run_fetcher_job")
    def test_run_github_fetcher(self, mock_run):
        from scheduler.jobs import run_github_fetcher

        run_github_fetcher()
        mock_run.assert_called_once()
        args = mock_run.call_args
        assert args.kwargs["source"] == "github"

    @patch("scheduler.jobs._run_fetcher_job")
    def test_run_news_fetcher(self, mock_run):
        from scheduler.jobs import run_news_fetcher

        run_news_fetcher()
        mock_run.assert_called_once()
        assert mock_run.call_args.kwargs["source"] == "news"

    @patch("scheduler.jobs._run_fetcher_job")
    def test_run_appstore_fetcher(self, mock_run):
        from scheduler.jobs import run_appstore_fetcher

        run_appstore_fetcher()
        mock_run.assert_called_once()
        assert mock_run.call_args.kwargs["source"] == "appstore"

    @patch("scheduler.jobs._run_fetcher_job")
    def test_run_twitter_fetcher(self, mock_run):
        from scheduler.jobs import run_twitter_fetcher

        run_twitter_fetcher()
        mock_run.assert_called_once()
        assert mock_run.call_args.kwargs["source"] == "twitter"

    @patch("scheduler.jobs._run_fetcher_job")
    def test_run_reddit_fetcher(self, mock_run):
        from scheduler.jobs import run_reddit_fetcher

        run_reddit_fetcher()
        mock_run.assert_called_once()
        assert mock_run.call_args.kwargs["source"] == "reddit"

    @patch("scheduler.jobs._run_fetcher_job")
    def test_run_playstore_fetcher(self, mock_run):
        from scheduler.jobs import run_playstore_fetcher

        run_playstore_fetcher()
        mock_run.assert_called_once()
        assert mock_run.call_args.kwargs["source"] == "playstore"

    @patch("scheduler.jobs._run_fetcher_job")
    def test_run_changelog_fetcher(self, mock_run):
        from scheduler.jobs import run_changelog_fetcher

        run_changelog_fetcher()
        mock_run.assert_called_once()
        assert mock_run.call_args.kwargs["source"] == "changelog"


@pytest.mark.django_db
class TestWeeklyDigestJob:
    """Tests for weekly_digest_job — email digest."""

    def test_no_users_sends_zero(self):
        from scheduler.jobs import weekly_digest_job

        weekly_digest_job()  # no users, should not crash

    @patch("django.core.mail.EmailMultiAlternatives")
    @patch("django.template.loader.render_to_string", return_value="<html>digest</html>")
    def test_user_with_alerts_sends_email(self, mock_render, mock_email_cls):
        from django.contrib.auth.models import User

        from metrics.tests.factories import AlertFactory
        from scheduler.jobs import weekly_digest_job

        user = User.objects.create_user("digest-user", "digest@test.com", "pass")
        user.profile.digest_enabled = True
        user.profile.save()
        e = EntityFactory()
        user.profile.watched_entities.add(e)
        AlertFactory(entity=e)

        mock_msg = MagicMock()
        mock_email_cls.return_value = mock_msg

        weekly_digest_job()
        mock_msg.send.assert_called_once()

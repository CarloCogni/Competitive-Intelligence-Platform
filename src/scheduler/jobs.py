# scheduler/jobs.py
"""
APScheduler job definitions.

Each job function fetches data for all configured entities and then
runs delta detection. A BatchRun record is created at the start of each
execution and updated at completion with per-entity success/failure detail.

Jobs are registered in start_scheduler(), which reads intervals and enabled
flags from FetcherConfig in the database.

Default intervals (set by data migration):
    GitHub    — every 24 hours (API rate limit: 60 req/hr unauthenticated)
    News      — every 2 hours  (Google RSS may rate-limit if too frequent)
    App Store — every 12 hours
"""

import logging
from contextlib import contextmanager
from typing import Callable, Generator

from apscheduler.schedulers.background import BackgroundScheduler
from django.utils import timezone
from django_apscheduler.jobstores import DjangoJobStore

logger = logging.getLogger(__name__)

# Module-level reference to the running scheduler, used by reschedule_job().
_scheduler: BackgroundScheduler | None = None

# Maps source key → (job_id, display_name, job_function).
# Populated after function definitions below.
SOURCE_TO_JOB: dict[str, tuple[str, str, Callable]] = {}

# Grace period in seconds: if Django restarts within this window after a
# missed job, APScheduler fires one catch-up run. coalesce=True ensures
# only one run fires regardless of how many intervals were missed.
MISFIRE_GRACE_TIME = 3600


# ---------------------------------------------------------------------------
# BatchRun context manager
# ---------------------------------------------------------------------------


@contextmanager
def batch_run_context(job_id: str) -> Generator:
    """
    Context manager that creates a BatchRun at entry and finalises it at exit.

    Sets status to RUNNING on entry. On normal exit, derives status from
    entities_success / entities_failed counts. On unhandled exception, sets
    status to FAILED and re-raises so APScheduler records the failure.

    Usage:
        with batch_run_context("github_fetcher") as run:
            run.entities_total = len(entities)
            for entity in entities:
                try:
                    ...
                    run.entities_success += 1
                except Exception as exc:
                    run.entities_failed += 1
                    run.error_log[entity.slug] = str(exc)
    """
    from scheduler.models import BatchRun

    now = timezone.now()
    batch = BatchRun.objects.create(
        job_id=job_id,
        triggered_at=now,
        started_at=now,
        status=BatchRun.Status.RUNNING,
    )
    try:
        yield batch
    except Exception as exc:
        batch.status = BatchRun.Status.FAILED
        batch.finished_at = timezone.now()
        batch.save(update_fields=["status", "finished_at"])
        logger.exception("Job '%s' raised an unhandled exception: %s", job_id, exc)
        raise
    else:
        # Derive final status from entity outcome counts
        if batch.entities_failed == 0:
            batch.status = BatchRun.Status.SUCCESS
        elif batch.entities_success == 0:
            batch.status = BatchRun.Status.FAILED
        else:
            batch.status = BatchRun.Status.PARTIAL

        batch.finished_at = timezone.now()
        batch.save(
            update_fields=[
                "status",
                "finished_at",
                "entities_total",
                "entities_success",
                "entities_failed",
                "error_log",
            ]
        )
        logger.info(
            "Job '%s' finished: %d/%d entities succeeded in %.1fs.",
            job_id,
            batch.entities_success,
            batch.entities_total,
            batch.duration_seconds or 0,
        )


# ---------------------------------------------------------------------------
# Generic fetcher runner
# ---------------------------------------------------------------------------


def _run_fetcher_job(
    source: str,
    display_name: str,
    fetcher_class_path: str,
    entity_queryset: object,
    post_fetch_hooks: list[Callable] | None = None,
) -> None:
    """
    Generic job runner that fetches data for all matching entities.

    Handles the fetch/store loop, per-entity error recording in BatchRun,
    and runs post-fetch detection hooks (delta, compound, release, etc.).
    """
    import importlib

    module_path, class_name = fetcher_class_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    fetcher_class = getattr(module, class_name)

    entities = list(entity_queryset)
    logger.info("%s fetcher: processing %d entity/entities.", display_name, len(entities))

    with batch_run_context(f"{source}_fetcher") as run:
        run.entities_total = len(entities)

        for entity in entities:
            try:
                fetcher = fetcher_class(entity)
                data = fetcher.fetch()
                if data:
                    fetcher.store(data)
                    run.entities_success += 1
                else:
                    run.entities_failed += 1
                    run.error_log[entity.slug] = "Fetcher returned no data"
            except Exception as exc:
                run.entities_failed += 1
                run.error_log[entity.slug] = str(exc)
                logger.exception("%s fetcher failed for '%s'.", display_name, entity.name)

        if post_fetch_hooks:
            for hook in post_fetch_hooks:
                hook()


# ---------------------------------------------------------------------------
# Job functions
# ---------------------------------------------------------------------------


def run_github_fetcher() -> None:
    """Fetch GitHub metrics for all entities with github_org configured."""
    from core.models import Entity
    from metrics.delta import run_compound_detection, run_delta_detection, run_release_detection

    _run_fetcher_job(
        source="github",
        display_name="GitHub",
        fetcher_class_path="fetchers.github.GithubFetcher",
        entity_queryset=Entity.objects.filter(github_org__gt=""),
        post_fetch_hooks=[
            lambda: run_delta_detection("github"),
            run_release_detection,
            run_compound_detection,
        ],
    )


def run_news_fetcher() -> None:
    """Fetch news articles for all entities with news_keywords configured."""
    from core.models import Entity
    from metrics.delta import run_competitor_detection, run_compound_detection, run_delta_detection
    from metrics.news_enrichment import run_news_enrichment

    _run_fetcher_job(
        source="news",
        display_name="News",
        fetcher_class_path="fetchers.news.NewsFetcher",
        entity_queryset=Entity.objects.exclude(news_keywords=[]),
        post_fetch_hooks=[
            lambda: run_delta_detection("news"),
            run_compound_detection,
            run_competitor_detection,
            run_news_enrichment,
        ],
    )


def run_appstore_fetcher() -> None:
    """Fetch App Store metrics for all entities with app_store_id configured."""
    from core.models import Entity
    from metrics.delta import run_compound_detection, run_delta_detection

    _run_fetcher_job(
        source="appstore",
        display_name="App Store",
        fetcher_class_path="fetchers.appstore.AppStoreFetcher",
        entity_queryset=Entity.objects.filter(app_store_id__gt=""),
        post_fetch_hooks=[
            lambda: run_delta_detection("appstore"),
            run_compound_detection,
        ],
    )


def run_twitter_fetcher() -> None:
    """Fetch Twitter/X metrics for all entities with twitter_handle configured."""
    from core.models import Entity
    from metrics.delta import run_compound_detection, run_delta_detection

    _run_fetcher_job(
        source="twitter",
        display_name="Twitter",
        fetcher_class_path="fetchers.twitter.TwitterFetcher",
        entity_queryset=Entity.objects.filter(twitter_handle__gt=""),
        post_fetch_hooks=[
            lambda: run_delta_detection("twitter"),
            run_compound_detection,
        ],
    )


def run_reddit_fetcher() -> None:
    """Fetch Reddit mention counts for all entities with reddit_keywords configured."""
    from core.models import Entity
    from metrics.delta import run_compound_detection, run_delta_detection

    _run_fetcher_job(
        source="reddit",
        display_name="Reddit",
        fetcher_class_path="fetchers.reddit.RedditFetcher",
        entity_queryset=Entity.objects.exclude(reddit_keywords=[]),
        post_fetch_hooks=[
            lambda: run_delta_detection("reddit"),
            run_compound_detection,
        ],
    )


def run_playstore_fetcher() -> None:
    """Fetch Play Store metrics for all entities with play_store_id configured."""
    from core.models import Entity
    from metrics.delta import run_compound_detection, run_delta_detection

    _run_fetcher_job(
        source="playstore",
        display_name="Play Store",
        fetcher_class_path="fetchers.playstore.PlayStoreFetcher",
        entity_queryset=Entity.objects.filter(play_store_id__gt=""),
        post_fetch_hooks=[
            lambda: run_delta_detection("playstore"),
            run_compound_detection,
        ],
    )


def run_changelog_fetcher() -> None:
    """Fetch changelog (releases.atom) for all entities with github_org+repo configured."""
    from core.models import Entity
    from metrics.delta import (
        run_changelog_release_detection,
        run_compound_detection,
        run_delta_detection,
    )

    _run_fetcher_job(
        source="changelog",
        display_name="Changelog",
        fetcher_class_path="fetchers.changelog.ChangelogFetcher",
        entity_queryset=Entity.objects.filter(github_org__gt="", github_repo__gt=""),
        post_fetch_hooks=[
            lambda: run_delta_detection("changelog"),
            run_changelog_release_detection,
            run_compound_detection,
        ],
    )


def daily_momentum_computation_job() -> None:
    """
    Recompute composite momentum scores for all tracked entities.

    Runs daily at 04:00 UTC (after all fetcher cycles have completed).
    Can also be triggered manually: python manage.py compute_momentum
    """
    from django.core.management import call_command

    logger.info("Daily momentum score computation started.")
    call_command("compute_momentum")
    logger.info("Daily momentum score computation complete.")


def weekly_digest_job() -> None:
    """
    Send a weekly alert digest email to all users with digest_enabled=True.

    For each user, queries the alerts they have NOT yet dismissed (no AlertSeen row)
    from the past 7 days, scoped to their watchlist. Skips users with empty watchlists
    or no unseen alerts. Outputs to the configured EMAIL_BACKEND (console in dev).
    """
    from django.contrib.auth import get_user_model
    from django.utils import timezone

    from metrics.models import Alert, AlertSeen

    User = get_user_model()
    cutoff = timezone.now() - timezone.timedelta(days=7)

    users = User.objects.filter(profile__digest_enabled=True).select_related("profile")
    sent = 0

    for user in users:
        if not user.email:
            continue

        watched = user.profile.watched_entities.all()
        if not watched.exists():
            continue

        seen_ids = AlertSeen.objects.filter(user=user).values_list("alert_id", flat=True)
        recent_alerts = list(
            Alert.objects.filter(
                entity__in=watched,
                created_at__gte=cutoff,
            )
            .exclude(pk__in=seen_ids)
            .select_related("entity")
            .order_by("-created_at")[:20]
        )

        if not recent_alerts:
            continue

        lines = [f"Weekly digest — {len(recent_alerts)} unseen alert(s) from your watchlist:\n"]
        for alert in recent_alerts:
            lines.append(
                f"  [{alert.get_alert_type_display()}] {alert.entity.name} — {alert.message}"
            )
        lines.append("\nLog in to review and dismiss alerts.")

        from django.conf import settings
        from django.core.mail import EmailMultiAlternatives
        from django.template.loader import render_to_string

        html_body = render_to_string(
            "email/weekly_digest.html",
            {
                "user": user,
                "alerts": recent_alerts,
                "alert_count": len(recent_alerts),
                "site_url": getattr(settings, "SITE_URL", "http://localhost:8000"),
            },
        )
        msg = EmailMultiAlternatives(
            subject="CompIntel — Weekly Digest",
            body="\n".join(lines),
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[user.email],
        )
        msg.attach_alternative(html_body, "text/html")
        msg.send(fail_silently=True)
        sent += 1
        logger.info("Digest sent to %s (%d alerts).", user.username, len(recent_alerts))

    logger.info("Weekly digest job complete: %d email(s) sent.", sent)


def monthly_anomaly_training_job() -> None:
    """
    Retrain Isolation Forest anomaly models for all (entity, metric_key) pairs
    where DeltaThreshold.use_dynamic_threshold is True.

    Runs on the 1st of each month at 02:00 UTC. LLM review is enabled so the
    quality gate catches unsuitable data before training. If Ollama/Groq is
    unavailable, the reviewer returns a safe "proceed" default — training
    is never blocked by LLM unavailability.

    Can also be run manually via: python manage.py train_anomaly_models
    """
    from django.core.management import call_command

    logger.info("Monthly anomaly model training started.")
    call_command("train_anomaly_models")
    logger.info("Monthly anomaly model training complete.")


def monthly_compound_optimization_job() -> None:
    """
    Retrain GA-optimized compound alert rules on the 1st of each month at 03:00 UTC.

    Deactivates previous rules and persists the new fittest set. Runs one hour
    after monthly_anomaly_training_job so fresh anomaly models are available.

    Can also be run manually via: python manage.py optimize_alert_rules
    """
    from django.core.management import call_command

    logger.info("Monthly compound rule optimization started.")
    call_command("optimize_alert_rules")
    logger.info("Monthly compound rule optimization complete.")


def weekly_briefing_job(briefing_pk: int | None = None) -> None:
    """
    Generate an AI-powered weekly intelligence briefing via Ollama.

    Pulls the last 7 days of alerts, metric deltas, and news items, builds
    a structured prompt, calls the local Ollama LLM, and persists the result
    as a WeeklyBriefing record (status=ready or failed).

    Runs every Sunday at 10:00 UTC, one hour after the email digest so fresh
    data is already in the DB when the briefing is generated.

    When `briefing_pk` is provided (on-demand path), the pre-created PENDING
    record is updated in-place rather than creating a new one. This ensures
    the HTMX polling endpoint (which knows that pk) observes the status change.
    """
    from briefings.generator import BriefingGenerator
    from briefings.models import WeeklyBriefing

    logger.info("Weekly briefing job started (briefing_pk=%s).", briefing_pk)

    existing = None
    if briefing_pk is not None:
        existing = WeeklyBriefing.objects.filter(pk=briefing_pk).first()
        if existing is None:
            logger.warning(
                "briefing_pk=%d not found in DB — will create a new record.", briefing_pk
            )

    briefing = BriefingGenerator().run(existing_briefing=existing)
    logger.info("Weekly briefing job complete: pk=%d status=%s.", briefing.pk, briefing.status)


# Build the map now that all job functions are defined.
SOURCE_TO_JOB = {
    "github": ("github_fetcher", "GitHub Fetcher", run_github_fetcher),
    "news": ("news_fetcher", "News Fetcher", run_news_fetcher),
    "appstore": ("appstore_fetcher", "App Store Fetcher", run_appstore_fetcher),
    "twitter": ("twitter_fetcher", "Twitter Fetcher", run_twitter_fetcher),
    "reddit": ("reddit_fetcher", "Reddit Fetcher", run_reddit_fetcher),
    "playstore": ("playstore_fetcher", "Play Store Fetcher", run_playstore_fetcher),
    "changelog": ("changelog_fetcher", "Changelog Fetcher", run_changelog_fetcher),
}


# ---------------------------------------------------------------------------
# Scheduler bootstrap
# ---------------------------------------------------------------------------


def start_scheduler() -> None:
    """
    Configure and start the APScheduler BackgroundScheduler.

    Jobs are stored in Django's database via DjangoJobStore so that
    execution history is visible in the admin. Intervals and enabled flags
    are read from FetcherConfig rows; if no row exists for a source,
    the job is skipped with a warning.

    On restart, existing jobs are loaded from the DB jobstore without
    resetting their next_run_time. If FetcherConfig's interval changed
    while the server was down, the interval is synced after start() once
    the jobstore is fully loaded and get_job() is reliable.

    misfire_grace_time=MISFIRE_GRACE_TIME (1 hour): if Django restarts
    within this window after a missed job, one catch-up run fires.
    coalesce=True: only one catch-up run fires regardless of how many
    intervals were missed (prevents run storms after long downtime).
    """
    global _scheduler

    from django_apscheduler.models import DjangoJob as DjangoJobModel

    from scheduler.models import FetcherConfig

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_jobstore(DjangoJobStore(), "default")

    configs = {cfg.source: cfg for cfg in FetcherConfig.objects.all()}

    # IDs already persisted in the DB — the jobstore loads these automatically
    # on start(). Do NOT call add_job() for them here; that causes ConflictingIdError
    # because get_job() returns None before start() loads the jobstore entries.
    existing_job_ids = set(DjangoJobModel.objects.values_list("id", flat=True))

    for source, (job_id, job_name, job_fn) in SOURCE_TO_JOB.items():
        cfg = configs.get(source)

        if cfg is None:
            # No config row yet (e.g. before first migrate). Skip gracefully.
            logger.warning("No FetcherConfig for '%s' — skipping job registration.", source)
            continue

        if not cfg.enabled:
            logger.info("'%s' fetcher disabled via FetcherConfig — skipping.", source)
            continue

        if job_id in existing_job_ids:
            # Existing job: let the jobstore reload it on start() so its
            # next_run_time is preserved. Interval sync happens after start().
            logger.info("%s found in DB — will load on start, then verify interval.", job_name)
        else:
            # New job (fresh DB or first deploy).
            _scheduler.add_job(
                job_fn,
                trigger="interval",
                hours=cfg.interval_hours,
                id=job_id,
                name=job_name,
                misfire_grace_time=MISFIRE_GRACE_TIME,
                coalesce=True,
            )
            logger.info("Scheduled %s every %dh (new job).", job_name, cfg.interval_hours)

    # Weekly digest — cron every Sunday at 09:00 UTC. Not stored in FetcherConfig
    # because it is user-facing, not a data-fetching job.
    if "weekly_digest" not in existing_job_ids:
        _scheduler.add_job(
            weekly_digest_job,
            trigger="cron",
            day_of_week="sun",
            hour=9,
            minute=0,
            id="weekly_digest",
            name="Weekly Digest",
            misfire_grace_time=MISFIRE_GRACE_TIME,
            coalesce=True,
        )
        logger.info("Scheduled Weekly Digest (Sundays 09:00 UTC).")

    # Monthly anomaly model training — cron on the 1st of each month at 02:00 UTC.
    if "monthly_anomaly_training" not in existing_job_ids:
        _scheduler.add_job(
            monthly_anomaly_training_job,
            trigger="cron",
            day=1,
            hour=2,
            minute=0,
            id="monthly_anomaly_training",
            name="Monthly Anomaly Model Training",
            misfire_grace_time=MISFIRE_GRACE_TIME,
            coalesce=True,
        )
        logger.info("Scheduled Monthly Anomaly Model Training (1st of each month, 02:00 UTC).")

    # Monthly compound rule optimization — cron on the 1st at 03:00 UTC (after anomaly training).
    if "monthly_compound_optimization" not in existing_job_ids:
        _scheduler.add_job(
            monthly_compound_optimization_job,
            trigger="cron",
            day=1,
            hour=3,
            minute=0,
            id="monthly_compound_optimization",
            name="Monthly Compound Rule Optimization",
            misfire_grace_time=MISFIRE_GRACE_TIME,
            coalesce=True,
        )
        logger.info("Scheduled Monthly Compound Rule Optimization (1st of each month, 03:00 UTC).")

    # Weekly AI briefing — cron every Sunday at 10:00 UTC (after digest so fresh data is present).
    if "weekly_briefing" not in existing_job_ids:
        _scheduler.add_job(
            weekly_briefing_job,
            trigger="cron",
            day_of_week="sun",
            hour=10,
            minute=0,
            id="weekly_briefing",
            name="Weekly AI Briefing",
            misfire_grace_time=MISFIRE_GRACE_TIME,
            coalesce=True,
        )
        logger.info("Scheduled Weekly AI Briefing (Sundays 10:00 UTC).")

    # Daily momentum score computation — cron at 04:00 UTC (after fetchers complete).
    if "daily_momentum" not in existing_job_ids:
        _scheduler.add_job(
            daily_momentum_computation_job,
            trigger="cron",
            hour=4,
            minute=0,
            id="daily_momentum",
            name="Daily Momentum Score Computation",
            misfire_grace_time=MISFIRE_GRACE_TIME,
            coalesce=True,
        )
        logger.info("Scheduled Daily Momentum Score Computation (04:00 UTC).")

    _scheduler.start()
    logger.info("APScheduler started.")

    # Post-start: sync intervals and fix misfire settings for existing jobs.
    # get_job() is now reliable because the jobstore is fully loaded.
    for source, (job_id, job_name, job_fn) in SOURCE_TO_JOB.items():
        cfg = configs.get(source)
        if cfg is None:
            continue

        job = _scheduler.get_job(job_id)
        if job is None:
            continue

        if not cfg.enabled:
            _scheduler.remove_job(job_id)
            logger.info("Removed job '%s' (disabled via FetcherConfig).", job_id)
            continue

        # Fix misfire_grace_time on jobs loaded from DB that predate this setting
        if getattr(job, "misfire_grace_time", None) != MISFIRE_GRACE_TIME or not job.coalesce:
            job.modify(misfire_grace_time=MISFIRE_GRACE_TIME, coalesce=True)
            logger.info("Updated misfire_grace_time/coalesce on '%s'.", job_id)

        # Sync interval if FetcherConfig changed while server was down
        existing_hours = job.trigger.interval.total_seconds() / 3600
        if abs(existing_hours - cfg.interval_hours) > 0.001:
            _scheduler.reschedule_job(job_id, trigger="interval", hours=cfg.interval_hours)
            logger.info(
                "Synced %s interval to %dh (was %.1fh in DB — changed while server was down).",
                job_name,
                cfg.interval_hours,
                existing_hours,
            )
        else:
            logger.info(
                "%s loaded from DB — next_run_time preserved: %s.",
                job_name,
                job.next_run_time,
            )


# ---------------------------------------------------------------------------
# Live rescheduling (called by post_save signal on FetcherConfig)
# ---------------------------------------------------------------------------


def reschedule_job(source: str) -> None:
    """
    Reschedule or remove a single job based on the current FetcherConfig.

    Called automatically when a FetcherConfig row is saved via Django admin.
    Does nothing if the scheduler has not been started yet.
    """
    global _scheduler

    if _scheduler is None or not _scheduler.running:
        logger.debug("reschedule_job('%s') called before scheduler started — no-op.", source)
        return

    if source not in SOURCE_TO_JOB:
        logger.warning("reschedule_job: unknown source '%s'.", source)
        return

    from scheduler.models import FetcherConfig

    job_id, job_name, job_fn = SOURCE_TO_JOB[source]

    try:
        cfg = FetcherConfig.objects.get(source=source)
    except FetcherConfig.DoesNotExist:
        logger.warning("reschedule_job: no FetcherConfig found for '%s'.", source)
        return

    if not cfg.enabled:
        # Remove the job if it currently exists.
        if _scheduler.get_job(job_id):
            _scheduler.remove_job(job_id)
            logger.info("Removed job '%s' (disabled via FetcherConfig).", job_id)
        return

    # Add or replace the job with the updated interval and correct misfire settings.
    _scheduler.add_job(
        job_fn,
        trigger="interval",
        hours=cfg.interval_hours,
        id=job_id,
        name=job_name,
        replace_existing=True,
        misfire_grace_time=MISFIRE_GRACE_TIME,
        coalesce=True,
    )
    logger.info("Rescheduled %s to every %dh.", job_name, cfg.interval_hours)

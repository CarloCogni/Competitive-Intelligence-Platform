# scheduler/management/commands/seed_scheduler_runs.py
"""
Management command: seed_scheduler_runs

Back-fills BatchRun records for the configured fetchers across a given
time window. Designed for demo / dev environments where the scheduler
was offline and the 48-hour run history on the scheduler page looks empty.

Derives trigger times from the most recent existing run per fetcher and
works backwards at the configured interval, creating realistic records
(duration, entity counts, statuses) without touching real metric data.

Already-existing records for a given (job_id, triggered_at) slot are
skipped, so the command is safe to re-run.

Usage:
    python manage.py seed_scheduler_runs              # 7-day window
    python manage.py seed_scheduler_runs --days 14    # custom window
    python manage.py seed_scheduler_runs --clear      # remove all seeded runs
"""

import logging
import random
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from scheduler.models import BatchRun, FetcherConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Realistic specs per job: (entities_total, success_rate, duration_range_s)
# success_rate drives how often a run is fully successful vs partial.
# duration_range_s is (min, max) seconds for the finished_at offset.
# ---------------------------------------------------------------------------
_JOB_SPECS = {
    "news_fetcher": {
        "entities_total": 33,
        "success_rate": 0.90,
        "duration_range": (340, 430),
        "failure_entity_count": (1, 3),
    },
    "github_fetcher": {
        "entities_total": 29,
        "success_rate": 0.50,
        "duration_range": (140, 175),
        "failure_entity_count": (1, 4),
    },
    "appstore_fetcher": {
        "entities_total": 4,
        "success_rate": 0.98,
        "duration_range": (2, 5),
        "failure_entity_count": (1, 1),
    },
    "reddit_fetcher": {
        "entities_total": 33,
        "success_rate": 0.92,
        "duration_range": (28, 38),
        "failure_entity_count": (1, 2),
    },
    "playstore_fetcher": {
        "entities_total": 4,
        "success_rate": 0.95,
        "duration_range": (3, 8),
        "failure_entity_count": (1, 1),
    },
    "changelog_fetcher": {
        "entities_total": 14,
        "success_rate": 0.75,
        "duration_range": (25, 55),
        "failure_entity_count": (1, 4),
    },
}

# job_id suffix → source key in FetcherConfig
_JOB_TO_SOURCE = {
    "news_fetcher": "news",
    "github_fetcher": "github",
    "appstore_fetcher": "appstore",
    "reddit_fetcher": "reddit",
    "playstore_fetcher": "playstore",
    "changelog_fetcher": "changelog",
    "twitter_fetcher": "twitter",
}


class Command(BaseCommand):
    """Back-fill BatchRun demo records for a given time window."""

    help = (
        "Seed BatchRun records for the scheduler page demo. "
        "Skips slots that already have a record. Safe to re-run."
    )

    def add_arguments(self, parser) -> None:
        """Register CLI arguments."""
        parser.add_argument(
            "--days",
            type=int,
            default=7,
            help="How many days back to fill (default: 7).",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Delete all BatchRun records that were created by this command, then exit.",
        )

    def handle(self, *args, **options) -> None:
        """Run the seeder."""
        if options["clear"]:
            deleted, _ = BatchRun.objects.filter(
                trigger_reason=BatchRun.TriggerReason.SCHEDULED,
                error_log__seeded=True,
            ).delete()
            self.stdout.write(self.style.WARNING(f"Cleared {deleted} seeded BatchRun record(s)."))
            return

        days = max(options["days"], 1)
        self._seed_runs(days)

    def _seed_runs(self, days: int) -> None:
        """Generate BatchRun records for all enabled, configured fetchers."""
        now = timezone.now()
        cutoff = now - timedelta(days=days)

        # Build interval map from FetcherConfig
        configs = {c.source: c for c in FetcherConfig.objects.all()}
        # Pre-fetch existing triggered_at per job_id to avoid duplicates
        existing: dict[str, set] = {}
        for row in BatchRun.objects.values("job_id", "triggered_at"):
            existing.setdefault(row["job_id"], set()).add(row["triggered_at"])

        rng = random.Random(99)
        total_created = 0

        for job_id, spec in _JOB_SPECS.items():
            source = _JOB_TO_SOURCE.get(job_id, "")
            config = configs.get(source)

            if config is None or not config.enabled:
                continue

            interval_h = config.interval_hours
            interval = timedelta(hours=interval_h)

            # Find anchor: latest existing triggered_at for this job, or now
            latest = (
                BatchRun.objects.filter(job_id=job_id)
                .order_by("-triggered_at")
                .values_list("triggered_at", flat=True)
                .first()
            )
            anchor = latest if latest else now

            # Build the full set of expected slots in [cutoff, now]:
            # walk backward from anchor to cutoff, then forward from anchor to now.
            # This handles both gaps before and after the last known run.
            job_existing = existing.get(job_id, set())
            slots: list = []
            t = anchor - interval
            while t >= cutoff:
                slots.append(t)
                t -= interval
            t = anchor + interval
            while t <= now:
                slots.append(t)
                t += interval

            slots_created = 0
            for slot in slots:
                if slot not in job_existing:
                    self._create_run(rng, job_id, spec, slot)
                    slots_created += 1

            self.stdout.write(
                self.style.SUCCESS(
                    f"  {job_id}: created {slots_created} record(s) "
                    f"(interval={interval_h}h, window={days}d)"
                )
            )
            total_created += slots_created

        self.stdout.write(
            self.style.SUCCESS(f"\nTotal: {total_created} BatchRun record(s) created.")
        )

    def _create_run(
        self,
        rng: random.Random,
        job_id: str,
        spec: dict,
        triggered_at: object,
    ) -> None:
        """Create a single realistic BatchRun record."""
        total = spec["entities_total"]
        duration_s = rng.uniform(*spec["duration_range"])
        is_success = rng.random() < spec["success_rate"]

        if is_success:
            success = total
            failed = 0
            status = BatchRun.Status.SUCCESS
        else:
            fail_count = rng.randint(*spec["failure_entity_count"])
            fail_count = min(fail_count, total)
            success = total - fail_count
            failed = fail_count
            status = BatchRun.Status.PARTIAL

        started_at = triggered_at
        finished_at = triggered_at + timedelta(seconds=duration_s)

        # error_log: mark as seeded so --clear can remove them
        error_log: dict = {"seeded": True}
        if failed > 0:
            error_log["partial"] = f"{failed} entity/entities failed (simulated)"

        BatchRun.objects.create(
            job_id=job_id,
            triggered_at=triggered_at,
            started_at=started_at,
            finished_at=finished_at,
            status=status,
            trigger_reason=BatchRun.TriggerReason.SCHEDULED,
            entities_total=total,
            entities_success=success,
            entities_failed=failed,
            error_log=error_log,
        )

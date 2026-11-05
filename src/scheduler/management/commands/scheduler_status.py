# scheduler/management/commands/scheduler_status.py
"""
Management command: print a snapshot of APScheduler job state from the DB.

Usage:
    python manage.py scheduler_status

Reads DjangoJob and DjangoJobExecution from the database — no running
scheduler process required. Useful for verifying that jobs are firing on
schedule and that past executions are being recorded.
"""

import logging
from datetime import timezone

from django.core.management.base import BaseCommand
from django_apscheduler.models import DjangoJob, DjangoJobExecution

logger = logging.getLogger(__name__)

# Number of recent executions to show per job.
_RECENT_EXECUTIONS = 5


class Command(BaseCommand):
    """Show scheduler job state and recent execution history."""

    help = "Print APScheduler job state and recent execution history from the DB."

    def handle(self, *args, **options) -> None:
        """Query DjangoJob and DjangoJobExecution and print a status table."""
        jobs = list(DjangoJob.objects.order_by("id"))

        if not jobs:
            self.stdout.write(self.style.WARNING("No scheduled jobs found in the database."))
            self.stdout.write(
                "Has the scheduler started at least once? Check RUN_SCHEDULER in .env."
            )
            return

        self.stdout.write(self.style.SUCCESS("\n=== Scheduled Jobs ==="))
        col = "{:<22} {:<30} {:<30} {:<20} {:<12}"
        self.stdout.write(
            col.format("Job ID", "Next Run (UTC)", "Last Run (UTC)", "Last Status", "Duration (s)")
        )
        self.stdout.write("-" * 115)

        for job in jobs:
            # Latest execution for this job, if any.
            last_exec = DjangoJobExecution.objects.filter(job=job).order_by("-run_time").first()

            next_run = (
                job.next_run_time.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                if job.next_run_time
                else "—"
            )
            last_run = (
                last_exec.run_time.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                if last_exec
                else "—"
            )
            status = last_exec.status if last_exec else "—"
            duration = f"{last_exec.duration:.2f}" if last_exec and last_exec.duration else "—"

            self.stdout.write(col.format(job.id, next_run, last_run, status, duration))

        self.stdout.write(
            self.style.SUCCESS(f"\n=== Recent Executions (last {_RECENT_EXECUTIONS} per job) ===")
        )
        exec_col = "{:<22} {:<30} {:<20} {:<12} {}"
        self.stdout.write(
            exec_col.format("Job ID", "Run Time (UTC)", "Status", "Duration (s)", "Exception")
        )
        self.stdout.write("-" * 115)

        for job in jobs:
            executions = list(
                DjangoJobExecution.objects.filter(job=job).order_by("-run_time")[
                    :_RECENT_EXECUTIONS
                ]
            )
            if not executions:
                self.stdout.write(exec_col.format(job.id, "—", "no executions yet", "—", ""))
                continue

            for exc in executions:
                run_time = exc.run_time.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                duration = f"{exc.duration:.2f}" if exc.duration else "—"
                exception = exc.exception or ""
                self.stdout.write(
                    exec_col.format(job.id, run_time, exc.status, duration, exception)
                )

        total = DjangoJobExecution.objects.count()
        self.stdout.write(self.style.SUCCESS(f"\nTotal execution records in DB: {total}"))

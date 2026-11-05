# metrics/management/commands/compute_momentum.py
"""
Management command: compute_momentum

Computes composite momentum scores for all tracked entities (or a single
entity). Scores are z-score-normalized 7-day velocities aggregated across
all available metrics.

Usage:
    python manage.py compute_momentum
    python manage.py compute_momentum --entity blockstream
    python manage.py compute_momentum --dry-run
"""

import logging

from django.core.management.base import BaseCommand

from metrics.momentum import compute_momentum_scores

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Compute composite momentum scores for tracked entities."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--entity",
            type=str,
            default=None,
            help="Compute only for this entity slug.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview scores without persisting to the database.",
        )

    def handle(self, *args, **options) -> None:
        entity_slug = options["entity"]
        dry_run = options["dry_run"]

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — scores will NOT be saved.\n"))

        results = compute_momentum_scores(
            entity_slug=entity_slug,
            dry_run=dry_run,
        )

        if not results:
            self.stdout.write(
                self.style.WARNING("No momentum scores computed (insufficient data).")
            )
            return

        # Print summary table
        self.stdout.write(f"\n{'Rank':<5} {'Entity':<35} {'Score':>8} {'Accel':>8} {'Metrics':>8}")
        self.stdout.write("-" * 68)

        for rank, r in enumerate(results, 1):
            accel_str = f"{r['acceleration']:+.4f}" if r["acceleration"] is not None else "   n/a"
            self.stdout.write(
                f"{rank:<5} {r['entity_name']:<35} {r['score']:>+8.4f} {accel_str:>8} {r['metrics_used']:>5}/15"
            )

        self.stdout.write(f"\n{len(results)} entity/entities scored.")

        if dry_run:
            self.stdout.write(self.style.WARNING("(dry run — nothing persisted)"))
        else:
            self.stdout.write(self.style.SUCCESS("Scores persisted to MomentumScore table."))

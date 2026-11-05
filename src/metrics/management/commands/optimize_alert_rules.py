# metrics/management/commands/optimize_alert_rules.py
"""
Management command: optimize_alert_rules

Runs a Genetic Algorithm to discover compound multi-metric alert rules
from historical data. Uses explicit AlertSeen feedback (useful=True/False)
as training labels. Intrinsic fitness signals (novelty, staleness, diversity)
allow the GA to evolve even without user interaction.

The fittest rules are persisted as CompoundRule rows, which are then
evaluated during each fetcher run by run_compound_detection().

Usage:
    python manage.py optimize_alert_rules
    python manage.py optimize_alert_rules --population-size 100 --generations 50
    python manage.py optimize_alert_rules --lookback-days 180 --top-k 5
    python manage.py optimize_alert_rules --dry-run
    python manage.py optimize_alert_rules --min-labeled 5
"""

import logging

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from metrics.ga_data import (
    build_historical_events,
    get_available_metric_keys,
    get_recent_deltas,
)
from metrics.ga_optimizer import FitnessEvaluator, GAEngine
from metrics.models import CompoundRule, GARun

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Run a Genetic Algorithm to discover compound multi-metric alert rules "
        "optimized against historical AlertSeen data."
    )

    def add_arguments(self, parser) -> None:
        """Define CLI arguments."""
        parser.add_argument(
            "--population-size",
            dest="population_size",
            type=int,
            default=80,
            help="GA population size (default: 80).",
        )
        parser.add_argument(
            "--generations",
            dest="generations",
            type=int,
            default=40,
            help="Number of GA generations (default: 40).",
        )
        parser.add_argument(
            "--mutation-rate",
            dest="mutation_rate",
            type=float,
            default=0.3,
            help="Per-individual mutation probability (default: 0.3).",
        )
        parser.add_argument(
            "--crossover-rate",
            dest="crossover_rate",
            type=float,
            default=0.7,
            help="Crossover probability (default: 0.7).",
        )
        parser.add_argument(
            "--lookback-days",
            dest="lookback_days",
            type=int,
            default=90,
            help="How far back to build historical events (default: 90).",
        )
        parser.add_argument(
            "--min-labeled",
            dest="min_labeled",
            type=int,
            default=10,
            help="Minimum dismissed alerts required to proceed (default: 10).",
        )
        parser.add_argument(
            "--top-k",
            dest="top_k",
            type=int,
            default=5,
            help="Number of best rules to persist (default: 5).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Evaluate and print rules but do not save to DB.",
        )
        parser.add_argument(
            "--no-deactivate-previous",
            action="store_true",
            default=False,
            help="Keep previous CompoundRules active (default: deactivate them).",
        )
        parser.add_argument(
            "--seed",
            dest="seed",
            type=int,
            default=None,
            help="Random seed for reproducibility.",
        )
        parser.add_argument(
            "--cold-start",
            action="store_true",
            default=False,
            help=(
                "Cold-start mode: bulk-dismiss all existing METRIC_SPIKE alerts "
                "as positive labels before running the GA. Use this on a fresh "
                "deployment to bootstrap the system. The GA will improve as real "
                "user dismissals accumulate over time."
            ),
        )

    def handle(self, *args, **options) -> None:
        """Run the GA optimization pipeline."""
        population_size = options["population_size"]
        generations = options["generations"]
        mutation_rate = options["mutation_rate"]
        crossover_rate = options["crossover_rate"]
        lookback_days = options["lookback_days"]
        min_labeled = options["min_labeled"]
        top_k = options["top_k"]
        dry_run = options["dry_run"]
        deactivate_previous = not options["no_deactivate_previous"]
        seed = options["seed"]
        cold_start = options["cold_start"]

        # Cold-start: seed AlertSeen records from existing alerts
        if cold_start:
            seeded = self._seed_cold_start()
            if seeded == 0:
                self.stdout.write(self.style.WARNING("No alerts available for cold-start seeding."))

        # Step 1: Build historical events
        self.stdout.write("Building historical events...")
        events = build_historical_events(lookback_days=lookback_days)

        if not events:
            raise CommandError(
                "No historical events could be built. "
                "Ensure DeltaThreshold rows exist and metric data is present."
            )

        # Step 2: Collect recent deltas for staleness checking
        recent_deltas = get_recent_deltas(weeks=4)
        self.stdout.write(f"  Recent delta snapshots: {len(recent_deltas)}")

        # Step 3: Validate labeled data
        evaluator = FitnessEvaluator(events, recent_deltas=recent_deltas)

        self.stdout.write(
            f"  Labeled positives: {evaluator.n_positives}  "
            f"Labeled negatives: {evaluator.n_negatives}"
        )

        has_labels = evaluator.n_positives >= min_labeled
        if not has_labels and not recent_deltas:
            raise CommandError(
                f"Insufficient training data: {evaluator.n_positives} positive labels "
                f"(need at least {min_labeled}) and no recent deltas for intrinsic signals. "
                f"Users must provide alert feedback or wait for metric data to accumulate."
            )
        if not has_labels:
            self.stdout.write(
                self.style.WARNING(
                    f"  Only {evaluator.n_positives} positive labels (need {min_labeled}). "
                    f"GA will rely on intrinsic signals (novelty, staleness, diversity)."
                )
            )

        # Step 4: Get available metric keys
        metric_keys = get_available_metric_keys()
        if len(metric_keys) < 2:
            raise CommandError(f"Need at least 2 configured metric keys, found {len(metric_keys)}.")

        self.stdout.write(f"  Metric keys: {len(metric_keys)}  Total events: {len(events)}")

        # Step 5: Create GARun record (unless dry-run)
        ga_run = None
        if not dry_run:
            ga_run = GARun.objects.create(
                population_size=population_size,
                generations=generations,
                mutation_rate=mutation_rate,
                crossover_rate=crossover_rate,
                labeled_positives=evaluator.n_positives,
                labeled_negatives=evaluator.n_negatives,
                config={
                    "lookback_days": lookback_days,
                    "min_labeled": min_labeled,
                    "top_k": top_k,
                    "seed": seed,
                    "metric_keys": metric_keys,
                },
            )

        # Step 6: Run GA
        self.stdout.write(
            f"\nRunning GA: pop={population_size}, gen={generations}, "
            f"mut={mutation_rate}, cx={crossover_rate}..."
        )

        try:
            engine = GAEngine(
                population_size=population_size,
                generations=generations,
                mutation_rate=mutation_rate,
                crossover_rate=crossover_rate,
                metric_keys=metric_keys,
                fitness_evaluator=evaluator,
                top_k=top_k,
                seed=seed,
            )
            result = engine.run()
        except Exception as exc:
            if ga_run is not None:
                ga_run.status = GARun.Status.FAILED
                ga_run.error_message = str(exc)
                ga_run.finished_at = timezone.now()
                ga_run.save(update_fields=["status", "error_message", "finished_at"])
            raise CommandError(f"GA failed: {exc}") from exc

        # Step 6: Print results
        self.stdout.write(
            f"\nGA complete: {result.generations_run} generations, "
            f"best_fitness={result.best_fitness:.4f}, "
            f"{len(result.best_rules)} rules discovered.\n"
        )

        if not result.best_rules:
            self.stdout.write(self.style.WARNING("No viable rules found."))
            if ga_run is not None:
                ga_run.status = GARun.Status.NO_RULES
                ga_run.best_fitness = 0.0
                ga_run.rules_produced = 0
                ga_run.finished_at = timezone.now()
                ga_run.save(
                    update_fields=["status", "best_fitness", "rules_produced", "finished_at"]
                )
            return

        # Print summary table
        self.stdout.write(f"  {'#':<4} {'Fitness':<10} {'Prec':<8} {'Recall':<8} {'Conditions'}")
        self.stdout.write(f"  {'—' * 4} {'—' * 10} {'—' * 8} {'—' * 8} {'—' * 40}")

        for i, genome in enumerate(result.best_rules, 1):
            metrics = evaluator.compute_metrics(genome)
            cond_str = " AND ".join(
                f"{c.metric_key} {c.operator} {c.threshold_pct * 100:.1f}%"
                for c in genome.conditions
            )
            self.stdout.write(
                f"  {i:<4} {evaluator(genome):<10.4f} "
                f"{metrics['precision']:<8.4f} {metrics['recall']:<8.4f} "
                f"{cond_str}"
            )

        if dry_run:
            self.stdout.write(self.style.WARNING("\nDry run — no rules saved."))
            return

        # Step 7: Persist rules
        if deactivate_previous:
            deactivated = CompoundRule.objects.filter(is_active=True).update(is_active=False)
            if deactivated:
                self.stdout.write(f"\nDeactivated {deactivated} previous rule(s).")

        rules_created = 0
        for genome in result.best_rules:
            metrics = evaluator.compute_metrics(genome)
            fitness = evaluator(genome)

            # Auto-generate a human-readable name
            name = " + ".join(c.metric_key.replace("_", " ").title() for c in genome.conditions)

            CompoundRule.objects.create(
                name=name,
                conditions=genome.to_condition_dicts(),
                fitness_score=fitness,
                precision=metrics["precision"],
                recall=metrics["recall"],
                f1_score=metrics["f1_score"],
                true_positives=metrics["true_positives"],
                false_positives=metrics["false_positives"],
                is_active=True,
                created_by_run=ga_run,
            )
            rules_created += 1

        # Step 8: Update GARun
        ga_run.status = GARun.Status.SUCCESS
        ga_run.best_fitness = result.best_fitness
        ga_run.rules_produced = rules_created
        ga_run.fitness_history = result.fitness_history
        ga_run.finished_at = timezone.now()
        ga_run.save(
            update_fields=[
                "status",
                "best_fitness",
                "rules_produced",
                "fitness_history",
                "finished_at",
            ]
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. {rules_created} compound rule(s) saved (GARun #{ga_run.pk})."
            )
        )

    def _seed_cold_start(self) -> int:
        """
        Bulk-dismiss existing METRIC_SPIKE alerts as neutral (useful=None).

        Uses the first superuser (or first user) as the dismissing user.
        Only seeds alerts that don't already have an AlertSeen record.
        Cold-start records are excluded from GA training — they exist only
        to clear the alert backlog, not to provide feedback signal.
        Returns the number of AlertSeen records created.
        """
        from django.contrib.auth import get_user_model

        from metrics.models import Alert, AlertSeen

        User = get_user_model()

        # Pick a user: prefer superuser, fall back to first user
        user = User.objects.filter(is_superuser=True).first()
        if user is None:
            user = User.objects.first()
        if user is None:
            self.stderr.write(self.style.ERROR("No users in database — cannot seed AlertSeen."))
            return 0

        # Dismiss all METRIC_SPIKE alerts that haven't been dismissed yet
        already_seen = set(AlertSeen.objects.filter(user=user).values_list("alert_id", flat=True))
        alerts_to_seed = Alert.objects.filter(
            alert_type=Alert.AlertType.METRIC_SPIKE,
        ).exclude(pk__in=already_seen)

        # useful=None: cold-start seeds are neutral, excluded from GA training
        records = [AlertSeen(user=user, alert=alert, useful=None) for alert in alerts_to_seed]
        created = AlertSeen.objects.bulk_create(records, ignore_conflicts=True)

        self.stdout.write(
            self.style.SUCCESS(
                f"Cold-start: dismissed {len(created)} METRIC_SPIKE alert(s) "
                f"as user '{user.username}' (useful=None, excluded from GA training)."
            )
        )
        return len(created)

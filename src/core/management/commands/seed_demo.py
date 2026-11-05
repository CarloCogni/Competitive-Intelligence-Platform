# core/management/commands/seed_demo.py
"""
Management command: seed_demo

Bootstraps a complete demo experience on a fresh instance. Creates a
superuser, alerts, GA runs, compound rules, threshold snapshots, and
user feedback records so the AI Observatory has data from first launch.

Fully idempotent — safe to re-run on every container start.

Usage:
    python manage.py seed_demo
"""

import logging
import random
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

from metrics.models import (
    Alert,
    AlertSeen,
    CompoundRule,
    GARun,
    NewsItem,
    ThresholdSnapshot,
)

logger = logging.getLogger(__name__)

User = get_user_model()

# Demo admin credentials
ADMIN_USERNAME = "batman"
ADMIN_PASSWORD = "the hero we deserve"
ADMIN_EMAIL = "batman@example.com"

# Fake GA runs: (months_ago, fitness, rules_produced, labeled_pos, labeled_neg, generations)
GA_RUN_SPECS = [
    (4, 0.35, 3, 10, 30, 40),
    (3, 0.52, 4, 18, 42, 40),
    (2, 0.68, 5, 26, 54, 40),
    (1, 0.78, 6, 34, 66, 40),
]

# Compound rule definitions linked to run index
COMPOUND_RULE_SPECS = [
    # (run_index, name, conditions, fitness, prec, recall, f1, tp, fp)
    (
        3,
        "Github Stars + News Article Count",
        [
            {"metric_key": "github_stars", "operator": "gte", "threshold_pct": 0.15},
            {"metric_key": "news_article_count", "operator": "gte", "threshold_pct": 1.0},
        ],
        0.780,
        0.88,
        0.72,
        0.79,
        18,
        3,
    ),
    (
        3,
        "Github Forks + Twitter Mention Count",
        [
            {"metric_key": "github_forks", "operator": "gte", "threshold_pct": 0.20},
            {"metric_key": "twitter_mention_count", "operator": "gte", "threshold_pct": 0.45},
        ],
        0.710,
        0.82,
        0.65,
        0.72,
        14,
        3,
    ),
    (
        2,
        "Appstore Rating + Github Open Issues",
        [
            {"metric_key": "appstore_rating", "operator": "lte", "threshold_pct": 0.08},
            {"metric_key": "github_open_issues", "operator": "gte", "threshold_pct": 0.25},
        ],
        0.650,
        0.78,
        0.60,
        0.68,
        11,
        3,
    ),
    (
        2,
        "News + Reddit + Github Stars",
        [
            {"metric_key": "news_article_count", "operator": "gte", "threshold_pct": 0.80},
            {"metric_key": "reddit_mention_count", "operator": "gte", "threshold_pct": 0.40},
            {"metric_key": "github_stars", "operator": "gte", "threshold_pct": 0.10},
        ],
        0.620,
        0.75,
        0.55,
        0.63,
        9,
        3,
    ),
    (
        1,
        "Github Stars + Github Forks",
        [
            {"metric_key": "github_stars", "operator": "gte", "threshold_pct": 0.25},
            {"metric_key": "github_forks", "operator": "gte", "threshold_pct": 0.25},
        ],
        0.520,
        0.70,
        0.50,
        0.58,
        7,
        3,
    ),
    (
        0,
        "News Article Count",
        [
            {"metric_key": "news_article_count", "operator": "gte", "threshold_pct": 2.0},
            {"metric_key": "twitter_total_engagement", "operator": "gte", "threshold_pct": 0.50},
        ],
        0.350,
        0.60,
        0.42,
        0.49,
        5,
        3,
    ),
]


class Command(BaseCommand):
    """Bootstrap a complete demo: superuser, alerts, GA runs, rules, thresholds, feedback."""

    help = (
        "Seed demo data for a fresh instance: admin user, alerts, "
        "GA optimization runs, compound rules, threshold snapshots, and feedback."
    )

    def handle(self, *args, **options) -> None:
        """Run all seeding steps in order."""
        self._create_superuser()
        self._seed_alerts()
        self._seed_compound_alerts()
        ga_runs = self._seed_ga_runs()
        self._seed_compound_rules(ga_runs)
        self._seed_threshold_snapshots()
        self._seed_alert_feedback()
        self._seed_news_enrichment()
        self.stdout.write(self.style.SUCCESS("\nseed_demo complete."))

    def _create_superuser(self) -> None:
        """Create the demo admin user (idempotent)."""
        if User.objects.filter(username=ADMIN_USERNAME).exists():
            self.stdout.write(f"  [SKIP] Superuser '{ADMIN_USERNAME}' already exists.")
            return

        User.objects.create_superuser(
            username=ADMIN_USERNAME,
            password=ADMIN_PASSWORD,
            email=ADMIN_EMAIL,
        )
        self.stdout.write(self.style.SUCCESS(f"  [OK]   Created superuser '{ADMIN_USERNAME}'."))

    def _seed_alerts(self) -> None:
        """Seed fake metric_spike alerts spread over 90 days (idempotent)."""
        existing = Alert.objects.filter(alert_type=Alert.AlertType.METRIC_SPIKE).count()
        if existing >= 50:
            self.stdout.write(f"  [SKIP] Metric spike alerts already exist ({existing}).")
            return

        call_command("seed_alerts", count=90, days=90, verbosity=0)
        self.stdout.write(
            self.style.SUCCESS("  [OK]   Seeded 90 demo metric_spike alerts (90d window).")
        )

    def _seed_compound_alerts(self) -> None:
        """Seed COMPOUND_SPIKE alerts over 90 days using active rules (idempotent)."""
        existing = Alert.objects.filter(alert_type=Alert.AlertType.COMPOUND_SPIKE).count()
        if existing >= 10:
            self.stdout.write(f"  [SKIP] Compound spike alerts already exist ({existing}).")
            return

        call_command("seed_alerts", count=0, days=90, compound=True, verbosity=0)
        seeded = Alert.objects.filter(alert_type=Alert.AlertType.COMPOUND_SPIKE).count()
        self.stdout.write(
            self.style.SUCCESS(f"  [OK]   Seeded {seeded} demo compound_spike alerts.")
        )

    def _seed_ga_runs(self) -> list[GARun]:
        """Seed fake GA optimization runs with increasing fitness (idempotent)."""
        if GARun.objects.filter(config__demo_seed=True).exists():
            self.stdout.write("  [SKIP] Demo GA runs already exist.")
            return list(GARun.objects.filter(config__demo_seed=True).order_by("started_at"))

        now = timezone.now()
        rng = random.Random(42)
        runs: list[GARun] = []

        for i, (months_ago, fitness, rules, pos, neg, gens) in enumerate(GA_RUN_SPECS):
            # Generate a realistic convergence curve
            history = _generate_fitness_history(fitness, gens, rng)

            run = GARun.objects.create(
                status=GARun.Status.SUCCESS,
                population_size=80,
                generations=gens,
                mutation_rate=0.3,
                crossover_rate=0.7,
                labeled_positives=pos,
                labeled_negatives=neg,
                best_fitness=fitness,
                rules_produced=rules,
                config={"demo_seed": True, "run_number": i + 1},
                fitness_history=history,
                finished_at=now - timedelta(days=months_ago * 30 - 1),
            )
            # Backdate started_at (auto_now_add prevents setting via constructor)
            started = now - timedelta(days=months_ago * 30)
            GARun.objects.filter(pk=run.pk).update(started_at=started)
            runs.append(run)

        self.stdout.write(self.style.SUCCESS(f"  [OK]   Seeded {len(runs)} demo GA runs."))
        return runs

    def _seed_compound_rules(self, ga_runs: list[GARun]) -> None:
        """Seed compound alert rules linked to GA runs (idempotent)."""
        if CompoundRule.objects.filter(created_by_run__config__demo_seed=True).exists():
            self.stdout.write("  [SKIP] Demo compound rules already exist.")
            return

        if not ga_runs:
            self.stdout.write("  [SKIP] No GA runs to link compound rules to.")
            return

        created = 0
        for run_idx, name, conditions, fitness, prec, recall, f1, tp, fp in COMPOUND_RULE_SPECS:
            if run_idx >= len(ga_runs):
                continue
            CompoundRule.objects.create(
                name=name,
                conditions=conditions,
                fitness_score=fitness,
                precision=prec,
                recall=recall,
                f1_score=f1,
                true_positives=tp,
                false_positives=fp,
                is_active=(run_idx >= 2),  # Only latest 2 runs' rules are active
                created_by_run=ga_runs[run_idx],
            )
            created += 1

        self.stdout.write(self.style.SUCCESS(f"  [OK]   Seeded {created} demo compound rules."))

    def _seed_threshold_snapshots(self) -> None:
        """Delegate threshold snapshot seeding to the dedicated command (idempotent)."""
        if ThresholdSnapshot.objects.filter(source__in=["cold_start", "dynamic"]).exists():
            count = ThresholdSnapshot.objects.count()
            self.stdout.write(f"  [SKIP] Threshold snapshots already exist ({count}).")
            return

        call_command("seed_threshold_history", verbosity=0)
        count = ThresholdSnapshot.objects.count()
        self.stdout.write(self.style.SUCCESS(f"  [OK]   Seeded {count} threshold snapshots."))

    def _seed_alert_feedback(self) -> None:
        """Seed AlertSeen records as GA training labels (idempotent)."""
        user = User.objects.filter(username=ADMIN_USERNAME).first()
        if user is None:
            self.stdout.write("  [SKIP] No admin user for alert feedback.")
            return

        if AlertSeen.objects.filter(user=user).exists():
            self.stdout.write("  [SKIP] Alert feedback already exists.")
            return

        alerts = list(Alert.objects.order_by("?")[:15])
        if not alerts:
            self.stdout.write("  [SKIP] No alerts to create feedback for.")
            return

        rng = random.Random(42)
        records: list[AlertSeen] = []
        for alert in alerts:
            roll = rng.random()
            if roll < 0.4:
                useful = True
            elif roll < 0.7:
                useful = False
            else:
                useful = None
            records.append(AlertSeen(user=user, alert=alert, useful=useful))

        AlertSeen.objects.bulk_create(records, ignore_conflicts=True)
        self.stdout.write(
            self.style.SUCCESS(f"  [OK]   Seeded {len(records)} alert feedback records.")
        )

    def _seed_news_enrichment(self) -> None:
        """Stamp a subset of existing NewsItems with synthetic enrichment data (idempotent)."""
        already = NewsItem.objects.filter(enriched_at__isnull=False).count()
        if already >= 50:
            self.stdout.write(f"  [SKIP] News enrichment data already exists ({already}).")
            return

        candidates = list(
            NewsItem.objects.filter(enriched_at__isnull=True).order_by("-published_at")[:200]
        )
        if not candidates:
            self.stdout.write("  [SKIP] No NewsItems available for enrichment seeding.")
            return

        rng = random.Random(42)
        now = timezone.now()

        sentiments = ["positive", "neutral", "negative", "mixed"]
        sentiment_weights = [0.25, 0.40, 0.20, 0.15]
        signal_types = [
            "product_launch",
            "partnership",
            "funding",
            "vulnerability",
            "regulation",
            "hiring",
            "opinion",
            "other",
        ]

        # Synthetic summaries keyed by signal type
        summary_templates = {
            "product_launch": [
                "New product release strengthens competitive position in the wallet market.",
                "Major version update introduces features targeting enterprise users.",
                "Hardware wallet refresh addresses community-requested security features.",
            ],
            "partnership": [
                "Strategic partnership expands distribution channel into new markets.",
                "Integration deal with major exchange increases platform accessibility.",
                "Cross-ecosystem collaboration signals broader industry alignment.",
            ],
            "funding": [
                "New funding round positions company for accelerated growth.",
                "Series funding at elevated valuation reflects investor confidence.",
                "Grant funding supports open-source development roadmap.",
            ],
            "vulnerability": [
                "Security disclosure prompts firmware update across device fleet.",
                "Reported vulnerability patched before any confirmed exploitation.",
                "Third-party audit identifies potential supply chain risk factors.",
            ],
            "regulation": [
                "Regulatory clarity in target market creates expansion opportunity.",
                "New compliance requirements affect product roadmap timeline.",
                "Industry-wide regulatory shift may advantage compliant players.",
            ],
            "hiring": [
                "Key engineering hire signals expansion into new technical domain.",
                "Leadership change reshapes product strategy direction.",
                "Team expansion accelerates delivery on published roadmap.",
            ],
            "opinion": [
                "Community sentiment highlights growing interest in self-custody solutions.",
                "Industry commentary reflects shifting attitudes toward sidechain technology.",
                "Analyst coverage draws attention to underappreciated competitive dynamics.",
            ],
            "other": [
                "Industry event attendance signals continued market engagement.",
                "Conference presentation showcases technical differentiation.",
                "Community activity suggests steady developer engagement.",
            ],
        }

        quote_templates = [
            "This development marks a significant milestone for the project.",
            "The market response indicates strong demand for this category of solutions.",
            "Industry observers note the competitive implications of this announcement.",
            "The timing of this release is particularly relevant given current market conditions.",
            "This positions the company well against established competitors in the space.",
        ]

        enriched = 0
        for item in candidates:
            sentiment = rng.choices(sentiments, weights=sentiment_weights, k=1)[0]
            signal = rng.choice(signal_types)
            summary = rng.choice(summary_templates[signal])
            quote = rng.choice(quote_templates)

            # Spread enrichment timestamps over the last 30 days
            days_ago = rng.uniform(0, 30)
            enriched_time = now - timedelta(days=days_ago)

            item.sentiment = sentiment
            item.signal_type = signal
            item.llm_summary = summary
            item.key_quote = quote
            item.enriched_at = enriched_time
            item.save(
                update_fields=[
                    "sentiment",
                    "signal_type",
                    "llm_summary",
                    "key_quote",
                    "enriched_at",
                ]
            )
            enriched += 1

        self.stdout.write(
            self.style.SUCCESS(f"  [OK]   Seeded enrichment data on {enriched} NewsItems.")
        )


def _generate_fitness_history(target_fitness: float, generations: int, rng: random.Random) -> list:
    """Generate a realistic convergence curve ending at target_fitness."""
    history: list[float] = []
    current = target_fitness * rng.uniform(0.08, 0.15)
    for gen in range(generations):
        # Fast early improvement, slower later (diminishing returns)
        progress = gen / generations
        step = (target_fitness - current) * rng.uniform(0.02, 0.12) * (1 - progress * 0.5)
        current = min(current + step, target_fitness)
        # Small noise
        current += rng.uniform(-0.005, 0.005)
        current = max(0.0, min(current, target_fitness))
        history.append(round(current, 4))
    # Ensure final value matches target
    history[-1] = target_fitness
    return history

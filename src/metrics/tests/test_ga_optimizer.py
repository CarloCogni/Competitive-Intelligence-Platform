# metrics/tests/test_ga_optimizer.py
"""Tests for the GA engine in metrics/ga_optimizer.py."""

from datetime import datetime

import pytest

from metrics.ga_optimizer import (
    MAX_CONDITIONS,
    MIN_CONDITIONS,
    CompoundRuleGenome,
    Condition,
    FitnessEvaluator,
    GAEngine,
    HistoricalEvent,
)

# ---------------------------------------------------------------------------
# Condition.is_met
# ---------------------------------------------------------------------------


class TestCondition:
    """Tests for Condition.is_met()."""

    def test_gte_met(self):
        cond = Condition("github_stars", "gte", 0.15)
        assert cond.is_met(0.20) is True

    def test_gte_not_met(self):
        cond = Condition("github_stars", "gte", 0.15)
        assert cond.is_met(0.10) is False

    def test_gte_exact_threshold(self):
        cond = Condition("github_stars", "gte", 0.15)
        assert cond.is_met(0.15) is True

    def test_lte_met(self):
        """lte checks for negative delta <= -threshold."""
        cond = Condition("appstore_rating", "lte", 0.10)
        assert cond.is_met(-0.15) is True

    def test_lte_not_met(self):
        cond = Condition("appstore_rating", "lte", 0.10)
        assert cond.is_met(0.05) is False

    def test_to_dict(self):
        cond = Condition("github_stars", "gte", 0.1567)
        d = cond.to_dict()
        assert d["metric_key"] == "github_stars"
        assert d["operator"] == "gte"
        assert d["threshold_pct"] == 0.1567


# ---------------------------------------------------------------------------
# CompoundRuleGenome.evaluate
# ---------------------------------------------------------------------------


class TestCompoundRuleGenome:
    """Tests for CompoundRuleGenome.evaluate()."""

    def test_all_conditions_met(self):
        genome = CompoundRuleGenome(
            [
                Condition("github_stars", "gte", 0.10),
                Condition("news_article_count", "gte", 1.00),
            ]
        )
        deltas = {"github_stars": 0.25, "news_article_count": 1.50}
        assert genome.evaluate(deltas) is True

    def test_one_condition_not_met(self):
        genome = CompoundRuleGenome(
            [
                Condition("github_stars", "gte", 0.10),
                Condition("news_article_count", "gte", 1.00),
            ]
        )
        deltas = {"github_stars": 0.25, "news_article_count": 0.50}
        assert genome.evaluate(deltas) is False

    def test_missing_metric_key_fails(self):
        """If a required metric key is absent from deltas, rule does not fire."""
        genome = CompoundRuleGenome(
            [
                Condition("github_stars", "gte", 0.10),
                Condition("appstore_rating", "gte", 0.05),
            ]
        )
        deltas = {"github_stars": 0.25}
        assert genome.evaluate(deltas) is False

    def test_empty_conditions_does_not_fire(self):
        genome = CompoundRuleGenome([])
        assert genome.evaluate({"github_stars": 0.25}) is False

    def test_metric_keys_property(self):
        genome = CompoundRuleGenome(
            [
                Condition("github_stars", "gte", 0.10),
                Condition("news_article_count", "gte", 1.00),
            ]
        )
        assert genome.metric_keys == {"github_stars", "news_article_count"}

    def test_to_condition_dicts(self):
        genome = CompoundRuleGenome(
            [
                Condition("github_stars", "gte", 0.10),
            ]
        )
        dicts = genome.to_condition_dicts()
        assert len(dicts) == 1
        assert dicts[0]["metric_key"] == "github_stars"


# ---------------------------------------------------------------------------
# FitnessEvaluator
# ---------------------------------------------------------------------------


def _make_event(label, **delta_overrides):
    """Helper to create a HistoricalEvent with default deltas."""
    deltas = {"github_stars": 0.01, "news_article_count": 0.01}
    deltas.update(delta_overrides)
    return HistoricalEvent(
        entity_id=1,
        timestamp=datetime(2025, 1, 1),
        deltas=deltas,
        label=label,
    )


class TestFitnessEvaluator:
    """Tests for FitnessEvaluator."""

    def test_perfect_rule(self):
        """A rule that catches all positives and no negatives gets high fitness."""
        # Positive events: big deltas. Negative events: small deltas.
        events = [
            _make_event(True, github_stars=0.30, news_article_count=2.0),
            _make_event(True, github_stars=0.25, news_article_count=1.50),
            _make_event(True, github_stars=0.35, news_article_count=1.80),
            _make_event(False, github_stars=0.01, news_article_count=0.01),
            _make_event(False, github_stars=0.02, news_article_count=0.05),
            _make_event(False, github_stars=0.01, news_article_count=0.02),
        ]
        evaluator = FitnessEvaluator(events)

        genome = CompoundRuleGenome(
            [
                Condition("github_stars", "gte", 0.20),
                Condition("news_article_count", "gte", 1.00),
            ]
        )
        fitness = evaluator(genome)
        assert fitness > 0.8

    def test_rule_with_only_false_positives(self):
        """A rule that only fires on negatives gets fitness 0."""
        events = [
            _make_event(True, github_stars=0.01),
            _make_event(False, github_stars=0.30),
            _make_event(False, github_stars=0.25),
            _make_event(False, github_stars=0.35),
        ]
        evaluator = FitnessEvaluator(events)
        genome = CompoundRuleGenome(
            [
                Condition("github_stars", "gte", 0.20),
            ]
        )
        fitness = evaluator(genome)
        assert fitness == 0.0

    def test_minimum_support_enforced(self):
        """Rules firing on fewer than MIN_SUPPORT events get fitness 0."""
        events = [
            _make_event(True, github_stars=0.30),
            _make_event(True, github_stars=0.01),
            _make_event(False, github_stars=0.01),
            _make_event(False, github_stars=0.01),
        ]
        evaluator = FitnessEvaluator(events)

        # This rule fires on exactly 1 event (< MIN_SUPPORT=3)
        genome = CompoundRuleGenome(
            [
                Condition("github_stars", "gte", 0.25),
            ]
        )
        fitness = evaluator(genome)
        assert fitness == 0.0

    def test_none_labels_excluded(self):
        """Events with label=None are not counted in fitness."""
        events = [
            _make_event(True, github_stars=0.30),
            _make_event(True, github_stars=0.25),
            _make_event(True, github_stars=0.35),
            _make_event(None, github_stars=0.40),  # unknown — excluded
            _make_event(False, github_stars=0.01),
        ]
        evaluator = FitnessEvaluator(events)
        assert evaluator.n_positives == 3
        assert evaluator.n_negatives == 1

    def test_compute_metrics(self):
        """compute_metrics returns precision, recall, F1, TP, FP."""
        events = [
            _make_event(True, github_stars=0.30),
            _make_event(True, github_stars=0.25),
            _make_event(True, github_stars=0.35),
            _make_event(False, github_stars=0.28),
            _make_event(False, github_stars=0.01),
        ]
        evaluator = FitnessEvaluator(events)
        genome = CompoundRuleGenome(
            [
                Condition("github_stars", "gte", 0.20),
            ]
        )
        metrics = evaluator.compute_metrics(genome)
        assert metrics["true_positives"] == 3
        assert metrics["false_positives"] == 1
        assert 0 < metrics["precision"] <= 1.0
        assert 0 < metrics["recall"] <= 1.0


# ---------------------------------------------------------------------------
# GAEngine
# ---------------------------------------------------------------------------


METRIC_KEYS = [
    "github_stars",
    "github_forks",
    "news_article_count",
    "appstore_rating",
    "twitter_mention_count",
]


class TestGAEngine:
    """Tests for GAEngine."""

    def test_initialize_population(self):
        """Population has the requested size with valid genomes."""
        events = [_make_event(True, github_stars=0.30)] * 5 + [
            _make_event(False, github_stars=0.01)
        ] * 5
        evaluator = FitnessEvaluator(events)
        engine = GAEngine(
            population_size=20,
            generations=1,
            mutation_rate=0.3,
            crossover_rate=0.7,
            metric_keys=METRIC_KEYS,
            fitness_evaluator=evaluator,
            seed=42,
        )
        pop = engine.initialize_population()
        assert len(pop) == 20
        for genome in pop:
            assert MIN_CONDITIONS <= len(genome.conditions) <= MAX_CONDITIONS
            # No duplicate keys within a genome
            keys = [c.metric_key for c in genome.conditions]
            assert len(keys) == len(set(keys))

    def test_crossover_produces_valid_offspring(self):
        """Crossover output respects bounds and has no duplicate keys."""
        events = [_make_event(True, github_stars=0.30)] * 5
        evaluator = FitnessEvaluator(events)
        engine = GAEngine(
            population_size=10,
            generations=1,
            mutation_rate=0.0,
            crossover_rate=1.0,
            metric_keys=METRIC_KEYS,
            fitness_evaluator=evaluator,
            seed=42,
        )
        parent1 = CompoundRuleGenome(
            [
                Condition("github_stars", "gte", 0.10),
                Condition("news_article_count", "gte", 0.50),
            ]
        )
        parent2 = CompoundRuleGenome(
            [
                Condition("github_forks", "gte", 0.20),
                Condition("appstore_rating", "gte", 0.05),
                Condition("twitter_mention_count", "gte", 0.30),
            ]
        )

        for _ in range(20):  # run multiple times for randomness coverage
            c1, c2 = engine.crossover(parent1, parent2)
            assert MIN_CONDITIONS <= len(c1.conditions) <= MAX_CONDITIONS
            assert MIN_CONDITIONS <= len(c2.conditions) <= MAX_CONDITIONS
            # No duplicate keys
            assert len(c1.metric_keys) == len(c1.conditions)
            assert len(c2.metric_keys) == len(c2.conditions)

    def test_mutation_respects_bounds(self):
        """Mutation keeps genome within MIN_CONDITIONS..MAX_CONDITIONS."""
        events = [_make_event(True, github_stars=0.30)] * 5
        evaluator = FitnessEvaluator(events)
        engine = GAEngine(
            population_size=10,
            generations=1,
            mutation_rate=1.0,  # high rate to exercise all paths
            crossover_rate=0.0,
            metric_keys=METRIC_KEYS,
            fitness_evaluator=evaluator,
            seed=42,
        )
        genome = CompoundRuleGenome(
            [
                Condition("github_stars", "gte", 0.10),
                Condition("news_article_count", "gte", 0.50),
            ]
        )

        for _ in range(50):
            mutated = engine.mutate(genome)
            assert MIN_CONDITIONS <= len(mutated.conditions) <= MAX_CONDITIONS
            for cond in mutated.conditions:
                assert cond.threshold_pct > 0

    def test_ga_converges_on_trivial_problem(self):
        """GA finds a rule that separates clearly labeled data."""
        # Positive events: big stars + big news. Negative: small both.
        events = []
        for _ in range(10):
            events.append(_make_event(True, github_stars=0.30, news_article_count=1.5))
        for _ in range(10):
            events.append(_make_event(False, github_stars=0.01, news_article_count=0.02))

        evaluator = FitnessEvaluator(events)
        engine = GAEngine(
            population_size=40,
            generations=30,
            mutation_rate=0.3,
            crossover_rate=0.7,
            metric_keys=METRIC_KEYS,
            fitness_evaluator=evaluator,
            top_k=3,
            seed=42,
        )
        result = engine.run()

        assert result.best_fitness > 0.5
        assert len(result.best_rules) > 0
        assert len(result.fitness_history) == 30

        # The best rule should fire on positive events and not on negatives
        best = result.best_rules[0]
        metrics = evaluator.compute_metrics(best)
        assert metrics["true_positives"] >= 5

    def test_too_few_metric_keys_raises(self):
        """GAEngine raises ValueError with fewer than MIN_CONDITIONS metric keys."""
        events = [_make_event(True, github_stars=0.30)] * 5
        evaluator = FitnessEvaluator(events)
        with pytest.raises(ValueError, match="at least"):
            GAEngine(
                population_size=10,
                generations=1,
                mutation_rate=0.3,
                crossover_rate=0.7,
                metric_keys=["only_one"],
                fitness_evaluator=evaluator,
            )

    def test_deduplication_in_results(self):
        """GA deduplicates identical rules in final output."""
        events = []
        for _ in range(10):
            events.append(_make_event(True, github_stars=0.30, news_article_count=1.5))
        for _ in range(10):
            events.append(_make_event(False, github_stars=0.01, news_article_count=0.02))

        evaluator = FitnessEvaluator(events)
        engine = GAEngine(
            population_size=20,
            generations=20,
            mutation_rate=0.1,
            crossover_rate=0.9,
            metric_keys=METRIC_KEYS,
            fitness_evaluator=evaluator,
            top_k=10,
            seed=42,
        )
        result = engine.run()

        # All returned rules should have unique condition signatures
        sigs = set()
        for rule in result.best_rules:
            sig = tuple(
                sorted(
                    (c.metric_key, c.operator, round(c.threshold_pct, 4)) for c in rule.conditions
                )
            )
            assert sig not in sigs
            sigs.add(sig)

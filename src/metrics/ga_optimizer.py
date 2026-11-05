# metrics/ga_optimizer.py
"""
Genetic Algorithm engine for compound alert rule optimization.

Pure Python + numpy — no Django imports. Django interaction happens at the
boundaries (data loading in ga_data.py, persistence in the management command).

Genome encoding:
    A CompoundRuleGenome is a variable-length list of Condition objects (2–5).
    Each Condition specifies a metric_key, comparison operator, and threshold.
    A rule fires when ALL conditions are satisfied (AND-conjunction).

Fitness:
    F-beta (beta=0.5, favors precision) computed by replaying each genome
    against labeled historical events. A small complexity penalty discourages
    unnecessarily complex rules.

Genetic operators:
    - Tournament selection (k=3) with elitism (top 2 survive)
    - Uniform crossover on condition lists
    - Four mutation types: threshold tweak, add/remove condition, replace key
"""

import logging
import math
import random
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

OPERATORS = ("gte", "lte")

# Genome constraints
MIN_CONDITIONS = 2
MAX_CONDITIONS = 5

# Fitness tuning
COMPLEXITY_PENALTY = 0.01
MIN_SUPPORT = 3  # rules firing on fewer events get fitness = 0
F_BETA = 0.5  # <1 favors precision over recall

# Intrinsic fitness signals (work without user interaction)
NOVELTY_WEIGHT = 0.05  # bonus for rules using rare/diverse metric keys
STALENESS_PENALTY = 0.05  # penalty for rules that wouldn't fire on recent data
DIVERSITY_THRESHOLD = 0.8  # Jaccard similarity above which diversity penalty applies
DIVERSITY_PENALTY = 0.1  # fitness reduction for near-duplicate rules


@dataclass(frozen=True)
class Condition:
    """Single condition: metric_key crosses a threshold in a given direction."""

    metric_key: str
    operator: str  # "gte" (delta >= threshold) or "lte" (delta <= threshold)
    threshold_pct: float  # fractional, e.g. 0.15 = 15%

    def is_met(self, delta: float) -> bool:
        """Return True if the observed delta satisfies this condition."""
        if self.operator == "gte":
            return delta >= self.threshold_pct
        # operator == "lte"
        return delta <= -self.threshold_pct

    def to_dict(self) -> dict:
        """Serialize to JSON-friendly dict."""
        return {
            "metric_key": self.metric_key,
            "operator": self.operator,
            "threshold_pct": round(self.threshold_pct, 4),
        }


@dataclass
class CompoundRuleGenome:
    """
    An individual in the GA population: variable-length list of Conditions.

    The rule fires when ALL conditions are met for a given entity's delta snapshot.
    """

    conditions: list[Condition] = field(default_factory=list)

    def evaluate(self, deltas: dict[str, float]) -> bool:
        """
        Return True if ALL conditions are satisfied by the given delta snapshot.

        Conditions referencing metric keys absent from `deltas` are treated
        as not met (the rule does not fire for entities missing that data).
        """
        if not self.conditions:
            return False

        for cond in self.conditions:
            delta = deltas.get(cond.metric_key)
            if delta is None:
                return False
            if not cond.is_met(delta):
                return False
        return True

    @property
    def metric_keys(self) -> set[str]:
        """Set of metric keys referenced by this genome's conditions."""
        return {c.metric_key for c in self.conditions}

    def to_condition_dicts(self) -> list[dict]:
        """Serialize conditions to list of dicts for DB storage."""
        return [c.to_dict() for c in self.conditions]


@dataclass(frozen=True)
class HistoricalEvent:
    """
    One labeled data point for fitness evaluation.

    Represents a (entity, time_window) pair with observed metric deltas and
    a label derived from AlertSeen records.
    """

    entity_id: int
    timestamp: datetime
    deltas: dict[str, float]  # {metric_key: pct_change}
    label: bool | None  # True=TP (dismissed), False=FP/TN, None=unknown


@dataclass(frozen=True)
class GAResult:
    """Output of a GA optimization run."""

    best_rules: list[CompoundRuleGenome]
    generations_run: int
    best_fitness: float
    fitness_history: list[float]  # best fitness per generation


# ---------------------------------------------------------------------------
# Fitness evaluation
# ---------------------------------------------------------------------------


class FitnessEvaluator:
    """
    Evaluate a genome's fitness by replaying it against labeled historical events.

    Scoring uses F-beta (beta=0.5, favoring precision) with a complexity penalty.
    Rules that fire on fewer than MIN_SUPPORT events receive fitness 0.

    Intrinsic signals (novelty bonus, staleness penalty) allow the GA to
    evolve even without user feedback.
    """

    def __init__(
        self,
        events: list[HistoricalEvent],
        recent_deltas: list[dict[str, float]] | None = None,
        key_frequencies: dict[str, float] | None = None,
    ) -> None:
        # Only use events with known labels for fitness calculation
        self._labeled_events = [e for e in events if e.label is not None]
        self._positive_events = [e for e in self._labeled_events if e.label is True]
        self._negative_events = [e for e in self._labeled_events if e.label is False]

        # Intrinsic signal data
        self._recent_deltas = recent_deltas or []
        self._key_frequencies = key_frequencies or {}

        if not self._labeled_events:
            logger.warning("FitnessEvaluator initialized with zero labeled events.")

    @property
    def n_positives(self) -> int:
        """Number of positive-labeled events."""
        return len(self._positive_events)

    @property
    def n_negatives(self) -> int:
        """Number of negative-labeled events."""
        return len(self._negative_events)

    def __call__(self, genome: CompoundRuleGenome) -> float:
        """
        Compute fitness for a genome.

        Base score: F-beta (from labeled events) minus complexity penalty.
        Intrinsic signals added on top:
            - Novelty bonus for rules using rare metric key combinations
            - Staleness penalty for rules that wouldn't fire on recent data

        Returns 0.0 if the rule fires on fewer than MIN_SUPPORT total events
        AND no intrinsic signals are available.
        """
        tp = 0
        fp = 0
        fn = 0
        total_fires = 0

        for event in self._labeled_events:
            fires = genome.evaluate(event.deltas)
            if fires:
                total_fires += 1

            if fires and event.label:
                tp += 1
            elif fires and not event.label:
                fp += 1
            elif not fires and event.label:
                fn += 1
            # TN: not fires and not event.label — not needed for F-beta

        # F-beta calculation (may be 0 if no labeled data exists)
        f_beta = 0.0
        has_labeled_data = bool(self._labeled_events)

        if has_labeled_data and total_fires >= MIN_SUPPORT and tp > 0:
            precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
            recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0

            if precision + recall > 0:
                beta_sq = F_BETA**2
                f_beta = (1 + beta_sq) * (precision * recall) / (beta_sq * precision + recall)
        elif has_labeled_data and total_fires < MIN_SUPPORT:
            # Not enough fires against labeled data — base score stays 0
            f_beta = 0.0

        # Complexity penalty: prefer simpler rules
        penalty = COMPLEXITY_PENALTY * len(genome.conditions)

        # --- Intrinsic signals ---

        # Novelty bonus: reward rules using rare/diverse metric keys
        novelty = self._compute_novelty(genome)

        # Staleness penalty: penalize rules that wouldn't fire on recent data
        staleness = self._compute_staleness(genome)

        fitness = f_beta - penalty + novelty - staleness
        return max(0.0, fitness)

    def _compute_novelty(self, genome: CompoundRuleGenome) -> float:
        """
        Compute novelty bonus based on how rare the genome's metric keys are.

        Uses the inverse of population-wide key frequencies. Keys that appear
        in fewer genomes contribute more novelty. Returns 0 if no frequency
        data is available.
        """
        if not self._key_frequencies or not genome.conditions:
            return 0.0

        # Average rarity across the genome's keys (rarity = 1 - frequency)
        rarities = []
        for cond in genome.conditions:
            freq = self._key_frequencies.get(cond.metric_key, 0.0)
            rarities.append(1.0 - freq)

        avg_rarity = sum(rarities) / len(rarities)
        return NOVELTY_WEIGHT * avg_rarity

    def _compute_staleness(self, genome: CompoundRuleGenome) -> float:
        """
        Compute staleness penalty for rules that wouldn't fire on recent data.

        If the rule doesn't fire on any of the recent delta snapshots, it gets
        penalized. Returns 0 if no recent data is available or the rule fires.
        """
        if not self._recent_deltas:
            return 0.0

        for deltas in self._recent_deltas:
            if genome.evaluate(deltas):
                return 0.0  # rule is still active — no penalty

        return STALENESS_PENALTY

    def compute_metrics(self, genome: CompoundRuleGenome) -> dict[str, float | int]:
        """
        Compute detailed precision/recall/F1 metrics for a genome.

        Used for persisting final results, not during evolution.
        """
        tp = fp = fn = 0

        for event in self._labeled_events:
            fires = genome.evaluate(event.deltas)
            if fires and event.label:
                tp += 1
            elif fires and not event.label:
                fp += 1
            elif not fires and event.label:
                fn += 1

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

        return {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1_score": round(f1, 4),
            "true_positives": tp,
            "false_positives": fp,
        }


# ---------------------------------------------------------------------------
# GA Engine
# ---------------------------------------------------------------------------


class GAEngine:
    """
    Genetic Algorithm for discovering compound alert rules.

    Population: variable-length CompoundRuleGenome individuals.
    Selection: tournament (k=3) with elitism (top 2 survive unchanged).
    Crossover: uniform swap of conditions between two parents.
    Mutation: threshold tweak, add/remove condition, replace metric key.
    """

    def __init__(
        self,
        population_size: int,
        generations: int,
        mutation_rate: float,
        crossover_rate: float,
        metric_keys: list[str],
        fitness_evaluator: FitnessEvaluator,
        top_k: int = 5,
        seed: int | None = None,
    ) -> None:
        self.population_size = population_size
        self.generations = generations
        self.mutation_rate = mutation_rate
        self.crossover_rate = crossover_rate
        self.metric_keys = list(metric_keys)
        self.fitness_evaluator = fitness_evaluator
        self.top_k = top_k

        if seed is not None:
            random.seed(seed)
            np.random.seed(seed)

        if len(self.metric_keys) < MIN_CONDITIONS:
            raise ValueError(
                f"Need at least {MIN_CONDITIONS} metric keys, got {len(self.metric_keys)}."
            )

    # --- Population initialization ---

    def _random_condition(self, exclude_keys: set[str] | None = None) -> Condition:
        """Create a random Condition, avoiding keys in `exclude_keys`."""
        available = (
            [k for k in self.metric_keys if k not in exclude_keys]
            if exclude_keys
            else self.metric_keys
        )
        if not available:
            available = self.metric_keys  # fallback: allow duplicates

        key = random.choice(available)
        operator = random.choice(OPERATORS)
        # Log-uniform threshold in [0.05, 2.0]
        threshold = math.exp(random.uniform(math.log(0.05), math.log(2.0)))
        return Condition(metric_key=key, operator=operator, threshold_pct=threshold)

    def _random_genome(self) -> CompoundRuleGenome:
        """Create a random genome with 2–4 conditions, no duplicate metric keys."""
        n_conditions = random.randint(MIN_CONDITIONS, min(4, len(self.metric_keys)))
        conditions: list[Condition] = []
        used_keys: set[str] = set()

        for _ in range(n_conditions):
            cond = self._random_condition(exclude_keys=used_keys)
            conditions.append(cond)
            used_keys.add(cond.metric_key)

        return CompoundRuleGenome(conditions=conditions)

    def initialize_population(self) -> list[CompoundRuleGenome]:
        """Create the initial random population."""
        return [self._random_genome() for _ in range(self.population_size)]

    # --- Selection ---

    def _tournament_select(
        self,
        population: list[CompoundRuleGenome],
        fitnesses: list[float],
        k: int = 3,
    ) -> CompoundRuleGenome:
        """Select one individual via tournament selection."""
        indices = random.sample(range(len(population)), min(k, len(population)))
        best_idx = max(indices, key=lambda i: fitnesses[i])
        return population[best_idx]

    # --- Crossover ---

    def crossover(
        self, parent1: CompoundRuleGenome, parent2: CompoundRuleGenome
    ) -> tuple[CompoundRuleGenome, CompoundRuleGenome]:
        """
        Uniform crossover on condition lists.

        For shared positions, randomly swap conditions. For excess conditions
        (when parents differ in length), include with 50% probability.
        Deduplicate metric keys afterward (keep lower threshold on conflict).
        """
        conds1 = list(parent1.conditions)
        conds2 = list(parent2.conditions)

        min_len = min(len(conds1), len(conds2))
        max_len = max(len(conds1), len(conds2))

        child1_conds: list[Condition] = []
        child2_conds: list[Condition] = []

        # Swap at shared positions
        for i in range(min_len):
            if random.random() < 0.5:
                child1_conds.append(conds2[i])
                child2_conds.append(conds1[i])
            else:
                child1_conds.append(conds1[i])
                child2_conds.append(conds2[i])

        # Handle excess conditions from the longer parent
        longer = conds1 if len(conds1) > len(conds2) else conds2
        for i in range(min_len, max_len):
            if random.random() < 0.5:
                child1_conds.append(longer[i])
            if random.random() < 0.5:
                child2_conds.append(longer[i])

        child1 = CompoundRuleGenome(self._deduplicate(child1_conds))
        child2 = CompoundRuleGenome(self._deduplicate(child2_conds))

        # Enforce min/max bounds
        child1 = self._enforce_bounds(child1)
        child2 = self._enforce_bounds(child2)

        return child1, child2

    def _deduplicate(self, conditions: list[Condition]) -> list[Condition]:
        """Remove duplicate metric keys, keeping the condition with the lower threshold."""
        seen: dict[str, Condition] = {}
        for cond in conditions:
            if cond.metric_key not in seen:
                seen[cond.metric_key] = cond
            elif cond.threshold_pct < seen[cond.metric_key].threshold_pct:
                seen[cond.metric_key] = cond
        return list(seen.values())

    def _enforce_bounds(self, genome: CompoundRuleGenome) -> CompoundRuleGenome:
        """Ensure genome has between MIN_CONDITIONS and MAX_CONDITIONS conditions."""
        conditions = list(genome.conditions)

        # Too few: add random conditions
        used_keys = {c.metric_key for c in conditions}
        while len(conditions) < MIN_CONDITIONS:
            cond = self._random_condition(exclude_keys=used_keys)
            conditions.append(cond)
            used_keys.add(cond.metric_key)

        # Too many: trim weakest (highest threshold = least selective)
        if len(conditions) > MAX_CONDITIONS:
            conditions.sort(key=lambda c: c.threshold_pct)
            conditions = conditions[:MAX_CONDITIONS]

        return CompoundRuleGenome(conditions=conditions)

    # --- Mutation ---

    def mutate(self, genome: CompoundRuleGenome) -> CompoundRuleGenome:
        """
        Apply mutations to a genome. Four independent mutation types:

        1. Threshold tweak: multiply a random condition's threshold by N(1.0, 0.2).
        2. Add condition: append a new random condition (if len < MAX_CONDITIONS).
        3. Remove condition: drop a random condition (if len > MIN_CONDITIONS).
        4. Replace metric key: swap a condition's key for a random unused one.

        Each type fires with probability mutation_rate / 4.
        """
        conditions = [
            Condition(c.metric_key, c.operator, c.threshold_pct) for c in genome.conditions
        ]
        sub_rate = self.mutation_rate / 4

        # 1. Threshold tweak
        if conditions and random.random() < sub_rate:
            idx = random.randrange(len(conditions))
            factor = max(0.01, random.gauss(1.0, 0.2))
            new_thresh = min(5.0, max(0.01, conditions[idx].threshold_pct * factor))
            old = conditions[idx]
            conditions[idx] = Condition(old.metric_key, old.operator, new_thresh)

        # 2. Add condition
        if len(conditions) < MAX_CONDITIONS and random.random() < sub_rate:
            used_keys = {c.metric_key for c in conditions}
            cond = self._random_condition(exclude_keys=used_keys)
            conditions.append(cond)

        # 3. Remove condition
        if len(conditions) > MIN_CONDITIONS and random.random() < sub_rate:
            idx = random.randrange(len(conditions))
            conditions.pop(idx)

        # 4. Replace metric key
        if conditions and random.random() < sub_rate:
            idx = random.randrange(len(conditions))
            used_keys = {c.metric_key for c in conditions} - {conditions[idx].metric_key}
            available = [k for k in self.metric_keys if k not in used_keys]
            if available:
                new_key = random.choice(available)
                old = conditions[idx]
                conditions[idx] = Condition(new_key, old.operator, old.threshold_pct)

        return CompoundRuleGenome(conditions=conditions)

    # --- Intrinsic population signals ---

    @staticmethod
    def _compute_key_frequencies(
        population: list[CompoundRuleGenome],
    ) -> dict[str, float]:
        """
        Compute how frequently each metric key appears across the population.

        Returns a dict mapping each metric key to a frequency in [0, 1].
        Used by FitnessEvaluator for novelty bonus calculation.
        """
        if not population:
            return {}
        key_counts: dict[str, int] = {}
        for genome in population:
            for key in genome.metric_keys:
                key_counts[key] = key_counts.get(key, 0) + 1
        pop_size = len(population)
        return {k: count / pop_size for k, count in key_counts.items()}

    @staticmethod
    def _apply_diversity_penalty(
        population: list[CompoundRuleGenome],
        fitnesses: list[float],
    ) -> list[float]:
        """
        Reduce fitness of near-duplicate genomes to maintain population diversity.

        For each pair with Jaccard similarity > DIVERSITY_THRESHOLD on metric
        keys, the weaker genome's fitness is reduced proportionally.
        """
        adjusted = list(fitnesses)
        n = len(population)
        for i in range(n):
            keys_i = population[i].metric_keys
            if not keys_i:
                continue
            for j in range(i + 1, n):
                keys_j = population[j].metric_keys
                if not keys_j:
                    continue
                # Jaccard similarity on metric keys
                intersection = len(keys_i & keys_j)
                union = len(keys_i | keys_j)
                similarity = intersection / union if union > 0 else 0.0

                if similarity > DIVERSITY_THRESHOLD:
                    # Penalize the weaker genome
                    penalty = DIVERSITY_PENALTY * similarity
                    if adjusted[i] <= adjusted[j]:
                        adjusted[i] = max(0.0, adjusted[i] - penalty)
                    else:
                        adjusted[j] = max(0.0, adjusted[j] - penalty)
        return adjusted

    # --- Main loop ---

    def run(self) -> GAResult:
        """
        Execute the genetic algorithm and return the top-K rules.

        Returns a GAResult with the best rules sorted by fitness, the number
        of generations run, the best fitness achieved, and a per-generation
        fitness history (best fitness per generation).
        """
        population = self.initialize_population()
        fitness_history: list[float] = []

        for gen in range(self.generations):
            # Update key frequencies for novelty calculation
            key_freqs = self._compute_key_frequencies(population)
            self.fitness_evaluator._key_frequencies = key_freqs

            # Evaluate fitness for all individuals
            fitnesses = [self.fitness_evaluator(ind) for ind in population]

            # Apply diversity penalty to discourage population convergence
            fitnesses = self._apply_diversity_penalty(population, fitnesses)

            best_fitness = max(fitnesses)
            fitness_history.append(best_fitness)

            if gen % 10 == 0 or gen == self.generations - 1:
                avg_fitness = sum(fitnesses) / len(fitnesses)
                logger.info(
                    "Generation %d/%d: best=%.4f avg=%.4f",
                    gen + 1,
                    self.generations,
                    best_fitness,
                    avg_fitness,
                )

            # Elitism: preserve top 2
            sorted_indices = sorted(
                range(len(population)), key=lambda i: fitnesses[i], reverse=True
            )
            elites = [population[sorted_indices[0]], population[sorted_indices[1]]]

            # Build next generation
            next_gen: list[CompoundRuleGenome] = list(elites)

            while len(next_gen) < self.population_size:
                parent1 = self._tournament_select(population, fitnesses)
                parent2 = self._tournament_select(population, fitnesses)

                # Crossover
                if random.random() < self.crossover_rate:
                    child1, child2 = self.crossover(parent1, parent2)
                else:
                    child1 = CompoundRuleGenome(list(parent1.conditions))
                    child2 = CompoundRuleGenome(list(parent2.conditions))

                # Mutation
                child1 = self.mutate(child1)
                child2 = self.mutate(child2)

                next_gen.append(child1)
                if len(next_gen) < self.population_size:
                    next_gen.append(child2)

            population = next_gen

        # Final evaluation and selection of top-K unique rules
        final_fitnesses = [self.fitness_evaluator(ind) for ind in population]
        sorted_indices = sorted(
            range(len(population)), key=lambda i: final_fitnesses[i], reverse=True
        )

        # Deduplicate: two genomes are identical if they have the same sorted condition set
        seen_signatures: set[tuple] = set()
        best_rules: list[CompoundRuleGenome] = []

        for idx in sorted_indices:
            if final_fitnesses[idx] <= 0:
                break

            genome = population[idx]
            sig = tuple(
                sorted(
                    (c.metric_key, c.operator, round(c.threshold_pct, 4)) for c in genome.conditions
                )
            )
            if sig in seen_signatures:
                continue
            seen_signatures.add(sig)
            best_rules.append(genome)

            if len(best_rules) >= self.top_k:
                break

        overall_best = max(final_fitnesses) if final_fitnesses else 0.0

        logger.info(
            "GA complete: %d generations, best_fitness=%.4f, %d unique rules discovered.",
            self.generations,
            overall_best,
            len(best_rules),
        )

        return GAResult(
            best_rules=best_rules,
            generations_run=self.generations,
            best_fitness=overall_best,
            fitness_history=fitness_history,
        )

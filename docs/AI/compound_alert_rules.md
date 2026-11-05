# GA-Optimized Compound Alert Rules

## AI Technique

**Genetic Algorithm (Evolutionary Computation) — automated discovery of multi-metric alert rules.** A population of candidate compound rules evolves over generations via tournament selection, crossover, and mutation. Each rule is an AND-conjunction of metric conditions (e.g., "github_stars up 15% AND news_article_count up 100%"). The fitness function replays rules against historical data labeled by user dismissals (AlertSeen records), optimizing for precision-weighted F-beta. The implementation is pure Python with numpy — no external GA library (DEAP) is used, keeping the dependency footprint minimal and the variable-length genome representation natural.

## The Problem

The existing alert system fires on single-metric thresholds: "github_stars changed more than 20%." Individually, these often produce noise. A 10% star increase is meaningless on its own. But 10% stars + 20% news mentions + a new release happening together is a genuine competitive signal.

Manually crafting compound rules is impractical — there are 16 metric keys, each with continuous thresholds and two possible directions (increase or decrease). The combinatorial space is too large for intuition. A Genetic Algorithm searches this space automatically, discovering rules that match the patterns behind alerts users actually found useful.

## How It Works

The system has three phases that run at different cadences.

### 1. Historical Event Building (during optimization)

The optimizer reconstructs labeled training data from the database:

- The lookback period (default 90 days) is divided into time windows per entity. Window size adapts automatically to the data span — 7-day windows for entities with months of history, down to 1-day windows for fresh deployments with only a few days of data.
- For each (entity, window) pair, the system computes the percent change for every configured metric key: latest value in the window vs. most recent value before the window.
- Each event is labeled using AlertSeen records:
  - **True (positive)** — An alert existed in this window and a user dismissed it within 14 days. The user found it useful enough to engage with.
  - **False (negative)** — An alert existed but was never dismissed after 14+ days (user saw it and ignored it), or no alert fired at all (nothing noteworthy happened).
  - **None (excluded)** — An alert exists but is too recent to judge (<14 days, no dismissal yet). Excluded from fitness evaluation to avoid mislabeling.

This logic lives in `metrics/ga_data.py`. The `build_historical_events()` function returns a list of `HistoricalEvent` dataclasses ready for the fitness evaluator.

### 2. GA Optimization (monthly or on-demand)

The Genetic Algorithm searches for compound rules that separate positive events (useful alerts) from negative events (noise or silence):

**Genome encoding:** Each individual is a `CompoundRuleGenome` — a variable-length list of 2–5 `Condition` objects. Each condition specifies a metric key, an operator (`gte` for increases, `lte` for decreases), and a fractional threshold. A rule fires when ALL conditions are satisfied simultaneously.

**Fitness function:** Each genome is replayed against all labeled historical events. The score is F-beta with beta=0.5 (favoring precision over recall — we want rules that fire meaningfully, not rules that catch everything). A small complexity penalty (0.01 per condition) discourages unnecessarily complex rules. Rules that fire on fewer than 3 historical events receive fitness 0 to prevent overfitting to lucky coincidences.

**Genetic operators:**
- **Selection:** Tournament selection (k=3) with elitism — the top 2 individuals survive unchanged into the next generation.
- **Crossover:** Uniform swap of conditions between two parents. When parents differ in length, excess conditions are inherited with 50% probability. Duplicate metric keys are resolved by keeping the lower threshold.
- **Mutation:** Four independent mutation types, each firing with probability `mutation_rate / 4`:
  1. Threshold tweak — multiply a random condition's threshold by a factor drawn from N(1.0, 0.2).
  2. Add condition — append a new random condition (if fewer than 5 conditions).
  3. Remove condition — drop a random condition (if more than 2 conditions).
  4. Replace metric key — swap one condition's key for a random unused key.

The top-K unique rules (default 5) are persisted as `CompoundRule` rows in the database, linked to a `GARun` metadata record that stores hyperparameters, label counts, and fitness history.

The GA engine lives in `metrics/ga_optimizer.py`. The management command that orchestrates the pipeline is `metrics/management/commands/optimize_alert_rules.py`.

### 3. Compound Detection (automatic, every fetch cycle)

When fetchers run and single-metric delta detection completes, `run_compound_detection()` evaluates all active compound rules:

- For each entity, the system computes the current percent change for all configured metric keys (same baseline logic as `run_delta_detection()`).
- Each active `CompoundRule` is evaluated against the entity's delta snapshot.
- If all conditions are met, a `COMPOUND_SPIKE` alert is created with metadata listing every triggered condition, its actual delta, and the rule's fitness score.
- Deduplication prevents the same rule from firing twice for the same entity within the lookback window.

Compound alerts appear in the dashboard with a purple left border and inline condition badges showing each triggered metric with its actual vs. threshold delta (e.g., "github_stars +22.3% (≥15%)").

This logic lives in `metrics/delta.py` in the `run_compound_detection()` function.

## Getting Started

### Step 1: Cold-start the system

On a fresh deployment, no AlertSeen records exist yet. Use cold-start mode to bulk-dismiss existing METRIC_SPIKE alerts as positive labels:

```
python manage.py optimize_alert_rules --cold-start --dry-run
```

The `--cold-start` flag dismisses all METRIC_SPIKE alerts as the first superuser, creating the positive labels the GA needs. The `--dry-run` flag lets you preview the results without saving rules. Remove `--dry-run` when satisfied.

Cold-start labels are imperfect — they treat all metric spikes as equally useful. The system improves over time as real users dismiss alerts selectively, providing higher-quality training signal.

### Step 2: Run the optimizer

```
python manage.py optimize_alert_rules --cold-start
```

The command builds historical events, validates that enough labeled data exists (default: 10 dismissed alerts), runs the GA, and persists the fittest rules. Previous rules are deactivated by default.

Output shows a summary table:

```
  #    Fitness    Prec     Recall   Conditions
  —    ——————     ————     ——————   ————————————————————————————————
  1    0.8234     0.9000   0.7500   github_stars gte 12.5% AND news_article_count gte 85.0%
  2    0.7891     0.8500   0.7000   github_commits_30d gte 18.3% AND twitter_mention_count gte 45.0%
  3    0.7456     0.8000   0.6500   appstore_rating lte 8.0% AND github_open_issues gte 25.0%

Done. 3 compound rule(s) saved (GARun #1).
```

If the GA runs but cannot find any viable compound rules (all candidates score below the minimum support threshold), the run is recorded with status `no_rules` instead of `success`. This is not a failure — the GA executed correctly but the labeled data did not support any high-quality multi-metric patterns. This typically happens on early runs with limited or low-quality training labels. As more user feedback accumulates (useful/noise dismissals), subsequent runs will have richer signal to work with.

### Step 3: Detection runs automatically

No action needed. Every time a fetcher runs (GitHub every 24h, News every 2h, etc.), `run_compound_detection()` fires after delta detection and evaluates all active rules. Compound alerts appear alongside single-metric alerts in the `/alerts/` page.

### Step 4: Review and manage rules in admin

Navigate to Django Admin → **Compound Rules** to view, activate, or deactivate individual rules. The `is_active` field is editable from the list view — toggle rules on/off without entering the detail page.

Navigate to **GA Runs** to review optimization history: hyperparameters, label counts, best fitness, and number of rules produced per run.

## Management Command Reference

```
python manage.py optimize_alert_rules [options]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--population-size N` | 80 | GA population size |
| `--generations N` | 40 | Number of GA generations |
| `--mutation-rate F` | 0.3 | Per-individual mutation probability |
| `--crossover-rate F` | 0.7 | Crossover probability |
| `--lookback-days N` | 90 | How far back to build historical events |
| `--min-labeled N` | 10 | Minimum dismissed alerts required to proceed |
| `--top-k N` | 5 | Number of best rules to persist |
| `--dry-run` | off | Evaluate and print rules but do not save |
| `--no-deactivate-previous` | off | Keep previous CompoundRules active |
| `--cold-start` | off | Bulk-dismiss existing METRIC_SPIKE alerts as positive labels |
| `--seed N` | none | Random seed for reproducibility |

## Scheduling

The optimizer runs automatically on the **1st of each month at 03:00 UTC** via APScheduler, one hour after the monthly anomaly model training (02:00 UTC). This ensures fresh Isolation Forest models are available before compound rule optimization begins.

Compound detection (`run_compound_detection()`) runs after every fetcher job — GitHub, News, App Store, Twitter, and Reddit. Since compound rules span multiple data sources, the function runs after each individual fetcher rather than waiting for all fetchers to complete. Deduplication prevents duplicate alerts across fetcher runs.

You can also run the optimizer manually at any time — the command is safe to re-run and deactivates previous rules by default.

## Architecture Reference

| File | Role |
|------|------|
| `metrics/ga_optimizer.py` | Core GA engine: `Condition`, `CompoundRuleGenome`, `FitnessEvaluator`, `GAEngine` |
| `metrics/ga_data.py` | Historical event builder: `build_historical_events()`, adaptive windowing |
| `metrics/delta.py` | Detection integration: `run_compound_detection()`, `_build_entity_deltas()` |
| `metrics/models.py` | `CompoundRule`, `GARun` models (statuses: `success`, `no_rules`, `failed`), `Alert.AlertType.COMPOUND_SPIKE` |
| `metrics/management/commands/optimize_alert_rules.py` | Management command with cold-start support |
| `metrics/admin.py` | `CompoundRuleAdmin` (list-editable `is_active`), `GARunAdmin` |
| `scheduler/jobs.py` | Monthly cron job, per-fetcher `run_compound_detection()` calls |
| `dashboard/templates/partials/alert_card.html` | Purple border + condition badges for compound alerts |
| `dashboard/templatetags/dashboard_tags.py` | `metric_source` filter returns "compound" for compound alerts |
| `dashboard/views.py` | "Compound" source filter in alerts view |

## Key Design Decisions

1. **No external GA library.** DEAP would add friction for variable-length genomes. The GA loop is ~150 lines of pure Python + numpy, keeping the genome representation natural and the dependency footprint zero.

2. **AlertSeen as implicit labels.** Users dismissing alerts (clicking ✕) in the dashboard creates training signal for the GA. No manual labeling workflow needed — the system learns from normal alert triage behavior.

3. **F-beta with beta=0.5 (precision-favored).** Alert fatigue is the primary risk. The GA optimizes for rules that fire meaningfully rather than rules that catch everything. A rule with 90% precision and 60% recall is preferred over one with 60% precision and 90% recall.

4. **Adaptive window sizing.** Fresh deployments with only days of data still produce training events. The window size shrinks automatically from 7 days to as low as 1 day based on each entity's actual data span. As history accumulates, windows grow to the standard 7-day size.

5. **Additive to existing alert system.** Compound detection runs alongside single-metric delta detection — it never modifies or replaces the existing system. Both can fire for the same entity at the same time. The compound alert explains the multi-signal pattern; the single-metric alert explains the individual change.

6. **Minimum support requirement.** Rules that fire on fewer than 3 historical events receive fitness 0, regardless of their precision. This prevents the GA from overfitting to one or two lucky coincidences.

7. **Complexity penalty.** Each additional condition costs 0.01 fitness points. This guides the GA toward simpler, more generalizable rules when multiple solutions have similar predictive power.

8. **Cold-start bootstrapping.** The `--cold-start` flag bulk-dismisses existing alerts to create initial training signal. This is explicitly imperfect — the labels improve as real users interact with the system. The GA is designed to be re-run monthly, incorporating progressively better training data.

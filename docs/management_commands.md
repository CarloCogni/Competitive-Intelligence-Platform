# Management Commands

All commands run from `src/` (where `manage.py` lives):

```bash
cd src
python manage.py <command> [options]
```

## Quick Reference

| Command | App | Purpose |
|---------|-----|---------|
| `seed` | core | Load `entities.json` into the database (idempotent) |
| `fetch` | core | Run data fetchers immediately, bypassing the scheduler |
| `scheduler_status` | scheduler | Print APScheduler job state from the database |
| `seed_alerts` | metrics | Create fake alerts for UI testing |
| `train_anomaly_models` | metrics | Train Isolation Forest models for dynamic thresholds |
| `optimize_alert_rules` | metrics | Run a Genetic Algorithm to discover compound alert rules |
| `backfill_release_urls` | metrics | Backfill missing `release_url` on NEW_RELEASE alerts |
| `compute_momentum` | metrics | Compute composite momentum scores (speed + acceleration) for all entities |
| `seed_momentum` | metrics | Seed synthetic momentum history for demo/development |

---

## Core

### `seed`

Loads verticals and entities from `entities.json` into the database. Idempotent — running it twice produces the same state with no duplicates. Uses `get_or_create` on the `slug` field for both `Vertical` and `Entity` models.

```bash
python manage.py seed                              # default: src/entities.json
python manage.py seed --file /path/to/custom.json  # custom file
```

| Option | Default | Description |
|--------|---------|-------------|
| `--file` | `src/entities.json` | Path to a custom entities JSON file |

---

### `fetch`

Triggers data fetchers immediately, bypassing the scheduler's interval. Runs delta detection after each source completes. Useful for bootstrapping a fresh database or forcing an on-demand refresh.

```bash
python manage.py fetch                                # all sources, all entities
python manage.py fetch --source github                # GitHub only
python manage.py fetch --source news                  # News only
python manage.py fetch --source appstore              # App Store only
python manage.py fetch --source playstore             # Play Store (Android) only
python manage.py fetch --source twitter               # Twitter/X only
python manage.py fetch --source reddit                # Reddit only
python manage.py fetch --source changelog             # Changelog (releases.atom) only
python manage.py fetch --entity blockstream            # all sources, one entity
python manage.py fetch --source github --entity jade   # one source, one entity
```

| Option | Default | Description |
|--------|---------|-------------|
| `--source` | `all` | Which fetcher to run: `github`, `news`, `appstore`, `playstore`, `twitter`, `reddit`, `changelog`, or `all` |
| `--entity` | all entities | Slug of a single entity to fetch |

When `--entity` is omitted, the command delegates to the same bulk job functions used by the scheduler. When `--entity` is provided, it instantiates individual fetcher classes and runs delta detection per source. The news fetcher also runs competitor candidate detection after each cycle.

---

## Scheduler

### `scheduler_status`

Prints a snapshot of APScheduler job state by reading `DjangoJob` and `DjangoJobExecution` tables directly. Does not require a running scheduler process — reads from the database only.

```bash
python manage.py scheduler_status
```

No options. Output includes:

- **Jobs table:** Job ID, next run time (UTC), last run time (UTC), last status, duration
- **Recent executions:** Last 5 executions per job with run time, status, duration, and exception (if any)
- **Total execution count** in the database

---

## Metrics

### `seed_alerts`

Creates realistic fake `Alert` rows for UI testing. Covers all metric sources (GitHub, News, App Store), all severity levels (LOW/MEDIUM/HIGH), and a mix of seen/unseen alerts spread over the last 14 days. Uses a fixed random seed (`42`) for reproducible output.

```bash
python manage.py seed_alerts                # create 20 alerts (default)
python manage.py seed_alerts --count 40     # create a custom number
python manage.py seed_alerts --clear        # delete ALL alerts and exit
python manage.py seed_alerts --clear --count 30  # clear first, then seed
```

| Option | Default | Description |
|--------|---------|-------------|
| `--count` | `20` | Number of fake alerts to create. Pass `0` to skip seeding |
| `--clear` | off | Delete all existing alerts. Used alone it only clears; combine with `--count` to reseed |

Severity is derived from a delta multiplier: `<2` = LOW, `2-5` = MEDIUM, `>=5` = HIGH. Approximately 30% of seeded alerts are marked as seen.

---

### `train_anomaly_models`

Trains Isolation Forest anomaly detection models for all `(entity, metric_key)` pairs where `DeltaThreshold.use_dynamic_threshold` is `True`.

- **Sufficient data** (>= `min_points` pct-change samples): trains a full Isolation Forest model, optionally gated by an LLM data-quality review.
- **Sparse data** (< `min_points`): attempts an LLM cold-start suggestion for an initial threshold.

```bash
python manage.py train_anomaly_models
python manage.py train_anomaly_models --min-points 20
python manage.py train_anomaly_models --entity blockstream --metric github_stars
python manage.py train_anomaly_models --skip-llm-review
python manage.py train_anomaly_models --use-groq
```

| Option | Default | Description |
|--------|---------|-------------|
| `--entity` | all | Limit to a single entity by slug |
| `--metric` | all | Limit to a single metric key (e.g. `github_stars`) |
| `--min-points` | `30` | Minimum pct-change data points required to train |
| `--skip-llm-review` | off | Bypass LLM data quality review and cold-start suggestions |
| `--use-groq` | off | Force Groq backend instead of Ollama |

Output summary reports counts for: trained, cold-start, skipped, and LLM-skipped pairs.

---

### `optimize_alert_rules`

Runs a Genetic Algorithm to discover compound multi-metric alert rules from historical data. Uses `AlertSeen` dismissals as implicit positive labels and old undismissed alerts as negatives. The fittest rules are persisted as `CompoundRule` rows, which are evaluated during each fetcher run by `run_compound_detection()`.

```bash
python manage.py optimize_alert_rules
python manage.py optimize_alert_rules --population-size 100 --generations 50
python manage.py optimize_alert_rules --lookback-days 180 --top-k 5
python manage.py optimize_alert_rules --dry-run
python manage.py optimize_alert_rules --cold-start
```

| Option | Default | Description |
|--------|---------|-------------|
| `--population-size` | `80` | GA population size |
| `--generations` | `40` | Number of GA generations |
| `--mutation-rate` | `0.3` | Per-individual mutation probability |
| `--crossover-rate` | `0.7` | Crossover probability |
| `--lookback-days` | `90` | How far back to build historical events |
| `--min-labeled` | `10` | Minimum dismissed alerts required to proceed |
| `--top-k` | `5` | Number of best rules to persist |
| `--dry-run` | off | Evaluate and print rules without saving to DB |
| `--no-deactivate-previous` | off | Keep previous CompoundRules active (default deactivates them) |
| `--seed` | none | Random seed for reproducibility |
| `--cold-start` | off | Bulk-dismiss existing METRIC_SPIKE alerts as positive labels before running the GA — useful for bootstrapping a fresh deployment |

Each GA run is recorded as a `GARun` row tracking population size, generations, fitness scores, and rule counts.

---

### `backfill_release_urls`

Populates missing `release_url` metadata on existing `NEW_RELEASE` alerts. For each alert that has a `release_tag` but no `release_url`, constructs the URL from `entity.github_org` + `entity.github_repo` + tag. Useful after deploying the release URL fix to patch older alerts.

```bash
python manage.py backfill_release_urls              # update alerts in-place
python manage.py backfill_release_urls --dry-run    # preview without saving
```

| Option | Default | Description |
|--------|---------|-------------|
| `--dry-run` | off | Print what would be updated without saving |

Alerts are skipped when: `release_url` is already populated, `release_tag` is missing, or the entity has no `github_org`/`github_repo` configured.

---

### `compute_momentum`

Computes composite momentum scores (speed + acceleration) for all tracked entities. Each entity's score is a z-score-normalized weighted average of 7-day velocities across all available metrics.

```bash
python manage.py compute_momentum                  # all entities
python manage.py compute_momentum --entity jade    # single entity
python manage.py compute_momentum --dry-run        # preview without saving
```

| Option | Default | Description |
|--------|---------|-------------|
| `--entity` | all | Compute only for this entity slug |
| `--dry-run` | off | Print ranked scores without persisting to database |

Output is a ranked table showing entity name, score, acceleration, and metric coverage. Scores are persisted as `MomentumScore` rows. Also runs daily at 04:00 UTC via APScheduler.

---

### `seed_momentum`

Generates synthetic momentum score history for demo and development. Produces realistic random-walk trajectories with entity-type-based drift (Blockstream entities trend slightly upward, sparse competitors trend slightly downward).

```bash
python manage.py seed_momentum                     # 90 days, all entities
python manage.py seed_momentum --clear             # wipe existing scores and reseed
python manage.py seed_momentum --days 60           # custom history length
```

| Option | Default | Description |
|--------|---------|-------------|
| `--clear` | off | Delete all existing MomentumScore rows before seeding |
| `--days` | `90` | Number of days of history to generate |

Seeds are deterministic per entity (MD5 hash of slug) for reproducible output.

---

## Typical Workflow

```bash
# 1. Seed entities from config
python manage.py seed

# 2. Fetch initial data from all sources
python manage.py fetch

# 3. (Optional) Seed fake alerts for UI testing
python manage.py seed_alerts

# 4. Train anomaly models once enough data accumulates
python manage.py train_anomaly_models

# 5. Optimize compound alert rules once users have dismissed enough alerts
python manage.py optimize_alert_rules

# 6. Check scheduler health
python manage.py scheduler_status
```

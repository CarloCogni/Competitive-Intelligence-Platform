# Momentum Tracking — Speed & Acceleration Analysis

## AI Technique

Cross-entity z-score normalization of 7-day metric velocities, producing a composite "momentum score" (speed) and its day-over-day derivative (acceleration). Enables quadrant-based competitive positioning: dominant, rising star, fading leader, stalling.

## The Problem

The dashboard tracks 15+ metrics per entity — GitHub stars, app store ratings, news mentions, social engagement. Each metric has its own scale, update frequency, and meaning. A human can spot that Entity A gained 500 stars this week, but cannot easily answer:

- **"Who is moving fastest overall?"** — requires comparing velocities across incompatible scales.
- **"Who is accelerating?"** — requires comparing the *change* in velocity, not the velocity itself.
- **"Who is about to overtake whom?"** — requires combining speed and acceleration into a trajectory forecast.

These are the questions that matter for strategic decision-making. An entity at #1 by raw metrics but decelerating is less threatening than one at #5 but accelerating fast.

## How It Works

### Phase 1: Velocity Computation

For each `(entity, metric_key)` pair, compute the 7-day velocity:

```
velocity = latest_value - value_7_days_ago
```

For inverted metrics (where lower is better, like `days_since_release`), the velocity is negated so that positive always means "improving."

### Phase 2: Cross-Entity Z-Score Normalization

Raw velocities are incomparable across metrics (stars vs. ratings vs. mentions). The system normalizes by computing the population mean and standard deviation of each metric's velocity across all entities:

```
z_score = (entity_velocity - population_mean) / population_stddev
```

A z-score of +1.5 means "this entity's velocity on this metric is 1.5 standard deviations above the average entity." This makes all metrics comparable on the same scale.

Z-scores are capped at ±3.0 to prevent a single viral event from dominating the composite.

### Phase 3: Composite Score

The entity's **momentum score (speed)** is the weighted average of its z-scores across all available metrics:

```
speed = Σ(weight_k × z_score_k) / Σ(weight_k)
```

Weights default to 1.0 but are configurable via `MomentumConfig` in Django admin.

Entities with fewer metrics are not penalized — the average is computed over available metrics only. The `metrics_used` field tracks coverage.

### Phase 4: Acceleration

**Acceleration** is the day-over-day change in the momentum score:

```
acceleration = speed_today - speed_yesterday
```

This captures whether an entity is *gaining* or *losing* speed. An entity with:
- High speed + positive acceleration → **Dominant** (fast and getting faster)
- Low speed + positive acceleration → **Rising Star** (slow but catching up)
- High speed + negative acceleration → **Fading Leader** (still ahead but losing steam)
- Low speed + negative acceleration → **Stalling** (falling behind, gap widening)

## Getting Started

### Compute momentum scores

```bash
python manage.py compute_momentum                  # all entities
python manage.py compute_momentum --entity jade    # single entity
python manage.py compute_momentum --dry-run        # preview without saving
```

### Seed demo history (90 days)

```bash
python manage.py seed_momentum                     # generate synthetic history
python manage.py seed_momentum --clear             # wipe and reseed
python manage.py seed_momentum --days 60           # custom period
```

### View the dashboard

Navigate to `/momentum/` in the dashboard. The page shows:

1. **Speed vs Acceleration scatter plot** — quadrant map with all entities
2. **Rankings table** — sortable by speed or acceleration
3. **Speed Over Time chart** — entity-filtered line chart (click pills to toggle)
4. **#1 Breakdown** — per-metric z-score bar chart for the top entity

Each entity's detail page also shows a **momentum badge** in the header.

## Management Command Reference

| Option | Default | Description |
|--------|---------|-------------|
| `--entity` | all | Compute only for this entity slug |
| `--dry-run` | off | Print scores without persisting to database |

## Scheduling

Momentum scores are recomputed daily at 04:00 UTC by `daily_momentum_computation_job` in the APScheduler configuration. The 7-day velocity window makes daily granularity sufficient — more frequent computation would not meaningfully change the scores.

## Architecture Reference

| File | Role |
|------|------|
| `metrics/models.py` | `MomentumScore` (per-entity per-day score) and `MomentumConfig` (weights/z-cap singleton) |
| `metrics/momentum.py` | Core computation: velocity → z-score → composite → acceleration |
| `metrics/management/commands/compute_momentum.py` | CLI entry point with `--entity` and `--dry-run` |
| `metrics/management/commands/seed_momentum.py` | Demo data generator (synthetic random walks) |
| `scheduler/jobs.py` | `daily_momentum_computation_job()` — daily cron at 04:00 UTC |
| `dashboard/views.py` | `momentum_leaderboard` view + `momentum_chart_data` JSON endpoint |
| `dashboard/templates/dashboard/momentum.html` | Scatter plot, sortable table, time-series chart, breakdown chart |
| `metrics/admin.py` | Admin registration for `MomentumScore` and `MomentumConfig` |

## Key Design Decisions

1. **Z-scores over percentile ranks** — with a small population (~30 entities), percentile ranks flatten magnitude differences. Z-scores preserve the signal that one entity is 3σ above the mean while another is only 0.5σ.

2. **Stored scores, not computed on-the-fly** — the computation requires ~600+ queries (all entities × all metrics × latest + baseline). Pre-computing daily and storing in `MomentumScore` avoids per-page-load overhead on SQLite.

3. **Average over available metrics** — an entity with 5/21 metrics is not penalized vs one with 15/21. The `metrics_used` field lets the UI show a coverage indicator so users can judge reliability.

4. **Inverted metric handling** — metrics where lower is better (`days_since_release`, `days_since_update`) have their velocity negated before z-scoring, so positive always means "improving."

5. **Z-score cap at ±3.0** — prevents a single viral event (e.g., 10,000 stars in one week) from dominating an entity's composite score. Configurable via `MomentumConfig.z_cap` in admin.

6. **Speed vs acceleration as separate dimensions** — the scatter plot's four quadrants directly answer the strategic question: "Who should we worry about?" Rising stars (top-left) are more actionable than dominant entities (top-right) because they represent *emerging* threats.

---

## Future Work: AI-Enhanced Momentum

The current implementation is purely statistical — z-scores and arithmetic. Several AI approaches could enhance the system:

### Unsupervised Learning: Anomaly-Aware Weighting

**Approach:** Use the existing Isolation Forest infrastructure to weight momentum signals by anomaly score. A metric velocity that is also flagged as anomalous (statistically unusual for that entity's own history) should contribute more to the composite than a velocity that is within normal range.

**Benefit:** Reduces noise from metrics that fluctuate routinely, amplifies signals that represent genuine regime changes.

**Implementation:** Multiply each z-score by a weight derived from the Isolation Forest anomaly score before averaging. Entities where multiple metrics are simultaneously anomalous would get disproportionately high momentum scores — exactly the "something is happening" signal you want.

### Genetic Algorithm: Learned Metric Weights

**Approach:** Use the existing GA infrastructure (`optimize_alert_rules`) to evolve optimal metric weights for the momentum composite. The fitness function would optimize for predicting which entities users subsequently investigate (click through to entity detail, add to watchlist, or generate briefings about).

**Benefit:** Replaces manual weight tuning with data-driven optimization. The system learns which metrics are most predictive of competitive relevance for *this specific user's* priorities.

**Implementation:** Extend `MomentumConfig.weights` to be GA-optimizable. Each individual in the GA population is a weight vector; fitness is measured by correlation between momentum rank and user engagement signals.

### LLM: Narrative Momentum Interpretation

**Approach:** Feed the momentum scores, per-metric breakdowns, and quadrant positions to an LLM and ask it to produce a strategic narrative — "Green Wallet is accelerating primarily due to a surge in GitHub activity and app store ratings, suggesting a coordinated product push."

**Benefit:** Transforms numbers into actionable intelligence. A non-technical stakeholder can read the narrative without understanding z-scores.

**Implementation:** Use the existing Ollama/Groq backend. Trigger on-demand from the momentum page or include as a section in the weekly briefing via `BriefingGenerator._build_context_block()`.

### Trajectory Forecasting

**Approach:** Given an entity's speed and acceleration time-series, fit a simple model (linear regression or ARIMA on the momentum score) to project where each entity will be in 7/14/30 days if current trends continue.

**Benefit:** Directly answers "when will Entity B overtake Entity A?" — the core strategic question.

**Implementation:** Add a forecast line to the Speed Over Time chart. Use scipy for curve fitting on the stored `MomentumScore` history.

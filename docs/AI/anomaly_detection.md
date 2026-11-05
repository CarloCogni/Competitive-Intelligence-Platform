# Statistical Anomaly Detection (Dynamic Thresholds)

## AI Technique

**Unsupervised Machine Learning — Isolation Forest for outlier detection.** Each (entity, metric_key) pair gets its own Isolation Forest model (scikit-learn) trained on historical period-over-period percent changes. The model learns what "normal variance" looks like for that specific entity and fires alerts only when a new data point deviates from established patterns. Training is unsupervised — no labelled anomalies are needed. An LLM assists during the training phase only: it acts as a quality gate on data suitability and suggests cold-start thresholds for entities with sparse history. Detection itself is purely statistical — deterministic, sub-millisecond, no LLM involved.

## The Problem

A 20% GitHub star increase means very different things for an entity with 500 stars (normal noise) vs. one with 50,000 stars (real event). Hardcoded percentage thresholds produce false positives for small, volatile entities and miss meaningful moves for large, stable ones.

Dynamic thresholds calibrate alert sensitivity per-entity based on that entity's actual historical variance.

## How It Works

The system has three phases that run at different cadences:

### 1. Training (monthly, automated)

For each `(entity, metric_key)` pair that opts into dynamic thresholds:

- Historical `Metric` values are loaded and converted into consecutive period-over-period **percent changes** (not raw values — training on raw values would flag all future growth of monotonically increasing metrics like GitHub stars as anomalous).
- An **Isolation Forest** model (scikit-learn) is fitted on those percent changes. The model learns what "normal variance" looks like for that specific entity and metric.
- The trained model is saved as a `.joblib` file under `src/data/anomaly_models/`.
- A **p95 absolute change** value is stored alongside the model and used for severity calculation.

Entities with fewer than 30 historical data points cannot train a reliable model. For these, an LLM can suggest a cold-start threshold based on the entity's profile and vertical category.

### 2. Detection (automatic, every fetch cycle)

When fetchers run and `run_delta_detection()` fires (this happens automatically via the scheduler), the system checks whether a `DeltaThreshold` has `use_dynamic_threshold=True`:

- **Dynamic path**: Loads the trained model from disk, scores the current percent change. If the model flags it as anomalous, an alert fires.
- **Cold-start path**: If the entity used LLM-suggested threshold (no trained model), compares the percent change directly against that threshold.
- **Fallback**: If no model file exists at all (e.g., training hasn't run yet), falls back to the static `threshold_pct` value.
- **Static path**: When `use_dynamic_threshold=False`, behavior is unchanged from the original system.

Detection never calls an LLM. It reads a pre-trained model file and runs a fast `predict()` call — deterministic and sub-millisecond.

### 3. LLM Review (during training only)

An LLM is integrated into the training pipeline with two roles:

**Training gate** — Before fitting a model, the LLM receives a statistical summary of the entity's data distribution (mean, std, percentiles, time span, monotonicity fraction) and returns a verdict:
- `proceed` — data looks suitable, train the model
- `skip` — data is unsuitable (e.g., flat-lined, strongly monotonic), do not train
- `warn` — proceed but flag a concern in the logs

**Cold-start suggestion** — For entities with insufficient history (<30 data points), the LLM reasons from entity characteristics (name, vertical, available data volume) to suggest a reasonable threshold. This avoids falling back blindly to a one-size-fits-all static value.

The LLM backend is **Ollama-first** (local, free) with **Groq API fallback** (cloud, for VPS deployment). All LLM failures are non-fatal — the system always returns a safe default and never blocks training.

## Getting Started

### Step 1: Enable dynamic thresholds

In Django Admin, go to **Delta Thresholds** and check the `use_dynamic_threshold` checkbox on the metric keys you want to enable (e.g., `github_stars`, `github_forks`). The static threshold remains as a fallback, so there's no risk.

### Step 2: Train models

Run the management command:

```
python manage.py train_anomaly_models
```

Output shows the status of each (entity, metric_key) pair:

```
  [OK]          entity='blockstream'      key='github_stars'    n=42
  [COLD-START]  entity='trezor'           key='appstore_rating' suggested=18% (small entity)
  [SKIP]        entity='jade'             key='github_forks'    n=8 (need 30)
  [LLM-SKIP]    entity='spiral'           key='github_stars'    n=35 (LLM: monotonic trend)

Done. trained=12, cold-start=3, skipped=8, llm-skipped=2.
```

### Step 3: Detection runs automatically

No action needed. Every time a fetcher runs (GitHub every 24h, News every 2h, etc.), `run_delta_detection()` fires and uses the trained models for entities with dynamic thresholds enabled.

## Management Command Reference

```
python manage.py train_anomaly_models [options]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--entity SLUG` | all | Limit training to one entity |
| `--metric KEY` | all | Limit to one metric key |
| `--min-points N` | 30 | Minimum percent-change samples required |
| `--skip-llm-review` | off | Bypass LLM quality gate and cold-start |
| `--use-groq` | off | Force Groq backend instead of Ollama |

## Scheduling

Training runs automatically on the **1st of each month at 02:00 UTC** via APScheduler (same mechanism as the weekly briefing and digest jobs).

You can also run it manually at any time — the command is idempotent and overwrites existing model files.

For development with sparse data, lower the minimum threshold:

```
python manage.py train_anomaly_models --min-points 5 --skip-llm-review
```

## Architecture Reference

| File | Role |
|------|------|
| `metrics/anomaly.py` | Core module: `AnomalyDetector`, `compute_pct_changes()`, dataclasses |
| `metrics/llm_review.py` | LLM client (Ollama/Groq) + `AnomalyLLMReviewer` |
| `metrics/delta.py` | Detection integration: dynamic guard clause in `run_delta_detection()` |
| `metrics/models.py` | `DeltaThreshold.use_dynamic_threshold` field |
| `metrics/management/commands/train_anomaly_models.py` | Training management command |
| `scheduler/jobs.py` | Monthly cron job registration |
| `config/settings/base.py` | `ANOMALY_MODEL_DIR`, `GROQ_API_KEY`, `GROQ_MODEL` |
| `data/anomaly_models/` | Trained model artifacts (gitignored) |

## Key Design Decisions

1. **Train on percent changes, not raw values.** A metric like `github_stars` only goes up. Training on raw values would flag every new reading as anomalous.

2. **LLM never runs at detection time.** Detection must be fast and deterministic. LLM is only involved during the monthly training step.

3. **All LLM failures are non-fatal.** If Ollama is down and no Groq key is set, the reviewer returns "proceed" and training continues with default parameters. The system never blocks on an LLM.

4. **Graceful fallback chain.** Dynamic model -> cold-start threshold -> static threshold. No alert path is ever broken by missing model files.

5. **Per-entity calibration.** Each `(entity, metric_key)` pair gets its own Isolation Forest model. An entity with 500 GitHub stars has completely different variance patterns than one with 50,000.

# Test Coverage Report

**Generated:** 2026-04-14
**Command:** `uv run pytest src/ --cov=src --cov-report=term-missing`
**Result:** 532 tests · 0 failures · **90% overall**

---

## Per-app summary

| App | Stmts | Missed | Cover |
|-----|------:|-------:|------:|
| briefings | 343 | 25 | 93% |
| config | 63 | 11 | 83% |
| core | 380 | 48 | 87% |
| dashboard | 1 257 | 276 | 78% |
| fetchers | 494 | 34 | 93% |
| metrics | 2 138 | 380 | 82% |
| scheduler | 361 | 90 | 75% |
| **TOTAL** | **9 328** | **924** | **90%** |

---

## Module-level detail

### briefings

| Module | Stmts | Miss | Cover | Missing lines |
|--------|------:|-----:|------:|---------------|
| admin.py | 35 | 4 | 89% | 55, 59, 80, 84 |
| email.py | 33 | 2 | 94% | 93-94 |
| generator.py | 185 | 17 | 91% | 245-250, 321-326, 369-370, 469, 476, 528-531, 580 |
| models.py | 77 | 2 | 97% | 158, 215 |

### core

| Module | Stmts | Miss | Cover | Missing lines |
|--------|------:|-----:|------:|---------------|
| admin.py | 22 | 1 | 95% | 40 |
| management/commands/fetch.py | 54 | 9 | 83% | 140-142, 147-153 |
| management/commands/seed.py | 42 | 0 | 100% | — |
| management/commands/seed_demo.py | 153 | 34 | 78% | 190-191, 242-243, 248, 280-281, 289-290, 313-418 |
| middleware.py | 29 | 2 | 93% | 40-42 |
| models.py | 62 | 0 | 100% | — |
| signals.py | 18 | 2 | 89% | 35-37 |

### dashboard

| Module | Stmts | Miss | Cover | Missing lines |
|--------|------:|-----:|------:|---------------|
| admin.py | 9 | 0 | 100% | — |
| forms.py | 10 | 0 | 100% | — |
| models.py | 18 | 0 | 100% | — |
| services.py | 10 | 0 | **100%** | — |
| templatetags/dashboard_tags.py | 76 | 13 | 83% | 157-169 |
| urls.py | 4 | 0 | 100% | — |
| views.py | 1 130 | 263 | 77% | (branch-level view logic: filter paths, chart helpers) |

### fetchers

| Module | Stmts | Miss | Cover | Missing lines |
|--------|------:|-----:|------:|---------------|
| appstore.py | 48 | 3 | 94% | 69-76 |
| base.py | 28 | 0 | 100% | — |
| changelog.py | 44 | 0 | 100% | — |
| github.py | 132 | 15 | 89% | 169-171, 177, 200-210, 234-236 |
| news.py | 63 | 6 | 90% | 70-72, 90-95, 136 |
| playstore.py | 38 | 0 | 100% | — |
| reddit.py | 60 | 5 | 92% | 114-120, 125, 137 |
| twitter.py | 54 | 5 | 91% | 65-69, 113-119 |
| utils.py | 27 | 0 | 100% | — |

### metrics

| Module | Stmts | Miss | Cover | Missing lines |
|--------|------:|-----:|------:|---------------|
| admin.py | 82 | 5 | 94% | 53, 67, 128, 162, 166 |
| anomaly.py | 88 | 0 | **100%** | — |
| delta.py | 352 | 54 | 85% | competitor aggregation/upsert branches |
| ga_data.py | 95 | 21 | 78% | 64-65, 117-143, 160, 316 |
| ga_optimizer.py | 316 | 13 | 96% | 175, 276-280, 297, 365, 473-474, 540, 564, 568, 662 |
| llm_review.py | 103 | 8 | 92% | 92-94, 113-115, 261, 333-334 |
| models.py | 217 | 9 | 96% | 103, 137-138, 187-188, 261, 461, 496-497 |
| momentum.py | 104 | 3 | **97%** | 87, 195, 222 |
| news_enrichment.py | 199 | 24 | 88% | 123-131, 218-219, 225, 239, 288-303, 347-348 |
| scraper.py | 35 | 0 | **100%** | — |
| management/commands/backfill_release_urls.py | 32 | 1 | 97% | 56 |
| management/commands/compute_momentum.py | 27 | 0 | **100%** | — |
| management/commands/optimize_alert_rules.py | 121 | 82 | 32% | GA engine loop (CPU-intensive) |
| management/commands/seed_alerts.py | 118 | 2 | 98% | 76, 79 |
| management/commands/seed_demo_alerts.py | 141 | 41 | 71% | compound/seen seeding branches |
| management/commands/seed_demo_metrics.py | 72 | 2 | 97% | 190, 218 |
| management/commands/seed_momentum.py | 72 | 9 | 88% | 68, 71, 73, 115, 117, 142-145 |
| management/commands/seed_threshold_history.py | 96 | 8 | 92% | 148, 237-257 |
| management/commands/train_anomaly_models.py | 141 | 101 | 28% | ML training loop (requires trained models) |

### scheduler

| Module | Stmts | Miss | Cover | Missing lines |
|--------|------:|-----:|------:|---------------|
| admin.py | 34 | 4 | 88% | 76-79 |
| jobs.py | 220 | 80 | 64% | start_scheduler(), register_all_jobs() (APScheduler integration) |
| management/commands/scheduler_status.py | 41 | 27 | 34% | 41-95 |
| management/commands/seed_scheduler_runs.py | 76 | 1 | 99% | 144 |
| models.py | 47 | 0 | **100%** | — |

---

## Known gaps and rationale

| Gap | Why untested |
|-----|-------------|
| `scheduler/jobs.py` start_scheduler (64%) | APScheduler integration: starts background threads, requires mocking the entire scheduler lifecycle. Fetcher dispatch and batch-run tracking are fully tested. |
| `dashboard/views.py` (77%) | Large view file (~2 900 lines); remaining uncovered lines are branch-level paths within views (filter combinations, chart data edge cases). All view endpoints return 200. |
| `metrics/management/commands/optimize_alert_rules.py` (32%) | GA engine optimization loop: CPU-intensive evolutionary search with tournament selection, crossover, mutation. Core GA primitives (Condition, Genome, FitnessEvaluator, GAEngine) are covered at 96% in test_ga_optimizer.py. |
| `metrics/management/commands/train_anomaly_models.py` (28%) | ML training pipeline: requires sufficient historical metric data and sklearn model artifacts. Core anomaly detection logic (AnomalyDetector) is covered at 100% in test_anomaly.py. |
| `config/settings/production.py` (0%) | Production-only settings; environment variables not available in test. |

---

## How to regenerate

```bash
uv run pytest src/ --cov=src --cov-report=term-missing
# HTML report:
uv run pytest src/ --cov=src --cov-report=html
start htmlcov/index.html     # Windows
```

---

## Exclusions

The following are excluded from measurement (no application logic):

- `*/migrations/*`
- `*/tests/*` and `*/tests.py`
- `manage.py`
- `*/wsgi.py`, `*/asgi.py`, `*/apps.py`
- `config/*` (Django project settings)

# Architecture Reference

This document is the deep-dive technical reference for CompIntel. It explains how the system is designed, how data flows through it, where decisions deviated from the original brief, and how to extend it.

**Audience:** developers working in the codebase, evaluators reviewing the design, and non-technical stakeholders who want to understand the system without reading code.

**Not covered here:** setup instructions, running fetchers, adding organisations, running tests. See [README.md](../README.md) for all of that.

### Document Map

| Document | What it covers |
|----------|----------------|
| [README.md](../README.md) | Quick start (local dev), running fetchers, adding organisations, running tests |
| [SPEC.md](../SPEC.md) | Implementation status vs. the original brief, out-of-scope features with PRD-level notes |
| [CLAUDE.md](../CLAUDE.md) | Code conventions, design rules, skill files, review stance |
| [docs/AI/overview.md](AI/overview.md) | Non-technical overview of all four AI subsystems |
| [docs/management_commands.md](management_commands.md) | Reference for all Django management commands |
| [Section 11 of this document](#11-scope-trade-offs) | ADR: why Django monolith over React + FastAPI |

---

## 1. System Overview

CompIntel is a competitive intelligence platform that automatically collects public signals — GitHub activity, news articles, app store ratings, social media mentions — for a set of tracked organisations and their competitors. It detects anomalies, fires alerts when metrics change significantly, and generates AI-powered intelligence briefings. All data is viewed through a server-rendered web dashboard.

The system is built around three layers plus two cross-cutting concerns:

```
  External APIs                    CompIntel                              User
 +-----------------+   +----------------------------------------------------+   +-----------+
 | GitHub REST API |   |                                                    |   |           |
 | Google News RSS |   |  Layer 1         Layer 2           Layer 3         |   |  Browser  |
 | iTunes API      +-->+  Fetchers  --->  Storage &    ---> Dashboard       +-->+  (HTMX)   |
 | Play Store      |   |  (fetchers/)     Detection         (dashboard/)    |   |           |
 | Twitter v2 API  |   |                  (metrics/)                        |   +-----------+
 | Reddit API      |   |                                                    |
 | GitHub Atom     |   |  Cross-cutting: Scheduler (scheduler/)             |
 +-----------------+   |  Cross-cutting: AI Briefings (briefings/)          |
                       |  Database: SQLite                                  |
                       +----------------------------------------------------+
```

### Data Flow Pipeline

This is the complete path from external API to user-visible output:

```
 Scheduler triggers fetcher job (interval-based)
      |
      v
 Fetcher.fetch() ---- hits external API (GitHub, RSS, iTunes, etc.)
      |
      v
 BaseFetcher.store() ---- creates MetricSnapshot (raw JSON, immutable audit trail)
      |                    creates Metric rows (flat key/value, queryable)
      v
 Post-fetch hooks run in sequence:
      |
      +-- run_delta_detection(source)
      |     Compares latest metric vs. baseline N days ago
      |     Static check: abs(pct_change) >= threshold_pct
      |     Dynamic check: Isolation Forest anomaly score
      |     --> fires METRIC_SPIKE alert
      |
      +-- run_release_detection()
      |     Compares two most recent GitHub snapshots
      |     --> fires NEW_RELEASE or NEW_REPO alert
      |
      +-- run_compound_detection()
      |     Evaluates GA-optimized multi-metric rules
      |     --> fires COMPOUND_SPIKE alert
      |
      +-- run_competitor_detection()  (news fetcher only)
            Scrapes article content, sends to LLM in batches
            Extracts untracked entity names with relevance scores
            --> creates CandidateEntity rows
            --> fires COMPETITOR_NEW alert after 3+ mentions
      |
      v
 Dashboard views query Metric, Alert, NewsItem
      |
      v
 Django renders HTML templates
      |
      v
 HTMX swaps partials into the DOM (polling + user actions)
```

---

## 2. Tech Stack

### What We Use

| Component | Technology | Why |
|-----------|-----------|-----|
| Web framework | Django 6.x | Batteries-included: ORM, admin, auth, templates, migrations. One codebase, one process. |
| Database | SQLite | Zero setup. No external service to install. Production-ready schema with indexes and constraints. |
| Scheduler | APScheduler via django-apscheduler | Embedded in the Django process — no separate worker, no message broker, no Redis. |
| Frontend interactivity | HTMX 1.9 + Alpine.js 3.x | Server-rendered partials with dynamic updates. No client-side state management, no build step. |
| Styling | Tailwind CSS (Play CDN) | Utility-first CSS loaded from CDN. No PostCSS, no purge config, no build pipeline. |
| Charts | Chart.js 4.x (CDN) | Works natively with Django templates. Data passed as inline JSON in template context. |
| Package manager | uv | Fast Python dependency resolution and virtual environment management. |
| HTTP client | requests | Standard library-adjacent. Used by all fetchers with `timeout=10`. |
| RSS parsing | feedparser | Parses Google News RSS and GitHub Atom feeds. |
| Machine learning | scikit-learn | Isolation Forest for anomaly detection. No deep learning dependencies. |
| Play Store scraping | google-play-scraper | No official Google Play API exists. This library scrapes the web interface. |
| Article extraction | trafilatura | Extracts article body text from URLs for LLM analysis. |
| HTML sanitization | nh3 | Sanitizes scraped HTML content before storage. Rust-backed, fast. |
| Static files | WhiteNoise | Serves and compresses static files from the Django process. No nginx needed in dev. |
| LLM backends | Ollama (local) + Groq (cloud) | Dual backend with automatic fallback. All LLM features degrade gracefully if unavailable. |

### Notable Non-Choices

| Rejected | Reason |
|----------|--------|
| **Celery + Redis** | APScheduler is sufficient for 7 fetcher jobs on hourly/daily intervals. No need for a separate broker process. |
| **React / Vue** | HTMX delivers the same UX for this read-heavy internal tool with far less complexity. No build step, no client-side routing. See [Section 11 of this document](#11-scope-trade-offs). |
| **FastAPI / Express** | Django's ORM, admin, auth, and template engine eliminate boilerplate that FastAPI would require building from scratch. |
| **Recharts / D3.js** | Chart.js works natively with server-rendered templates. No npm, no bundler. |
| **Django REST Framework** | No external API consumers exist. All rendering is server-side. DRF can be added alongside existing views when needed ([migration path in SPEC.md](../SPEC.md)). |
| **PostgreSQL** | SQLite is zero-setup and sufficient for single-digit concurrent users. PostgreSQL is the first production upgrade (see [Roadmap](#14-production-upgrade-roadmap)). |

---

## 3. Project Structure

```
src/
├── manage.py
├── entities.json                    # Config: all tracked organisations (7 verticals, 31 entities)
│
├── config/                          # Django project configuration
│   ├── settings/
│   │   └── base.py                  #   All settings (DB, apps, middleware, LLM config)
│   ├── urls.py                      #   Root URL router: admin, accounts, dashboard
│   └── wsgi.py
│
├── core/                            # Shared domain models
│   ├── models.py                    #   Vertical, Entity, UserProfile
│   ├── admin.py
│   ├── middleware.py                #   TimezoneMiddleware (applies user's timezone)
│   └── management/commands/
│       └── seed.py                  #   Loads entities.json into DB (idempotent)
│
├── fetchers/                        # Layer 1: data ingestion
│   ├── base.py                      #   BaseFetcher abstract class (fetch/extract/store lifecycle)
│   ├── github.py                    #   GitHub REST API (stars, forks, commits, releases)
│   ├── news.py                      #   Google News RSS (articles, deduplication)
│   ├── appstore.py                  #   iTunes Search API (iOS rating, review count)
│   ├── playstore.py                 #   google-play-scraper (Android rating, review count)
│   ├── twitter.py                   #   Twitter v2 API (mentions, engagement)
│   ├── reddit.py                    #   Reddit public API (mention count)
│   ├── changelog.py                 #   GitHub releases.atom RSS (release detection)
│   └── utils.py                     #   Shared helpers: parse_date(), to_float()
│
├── metrics/                         # Layer 2: storage + detection + AI
│   ├── models.py                    #   MetricSnapshot, Metric, Alert, DeltaThreshold,
│   │                                #   ThresholdSnapshot, NewsItem, CandidateEntity,
│   │                                #   AlertSeen, GARun, CompoundRule
│   ├── delta.py                     #   Delta detection, release detection, compound detection,
│   │                                #   competitor detection (LLM pipeline)
│   ├── anomaly.py                   #   Isolation Forest anomaly detector
│   ├── genetic_algorithm.py         #   GA optimizer for compound alert rules
│   ├── llm_client.py               #   Dual LLM backend (Ollama + Groq fallback)
│   ├── scraper.py                   #   Article content scraping (trafilatura)
│   └── admin.py
│
├── scheduler/                       # Job orchestration
│   ├── models.py                    #   FetcherConfig (per-source intervals), BatchRun (execution log)
│   ├── jobs.py                      #   Job definitions, start_scheduler(), reschedule_job()
│   └── apps.py                      #   Auto-starts scheduler on Django ready()
│
├── dashboard/                       # Layer 3: views + templates
│   ├── views.py                     #   All view functions (~30 views)
│   ├── urls.py                      #   URL patterns (~50 routes)
│   ├── models.py                    #   SavedComparison
│   ├── services.py                  #   Edge computation, node sizing, profile settings
│   ├── admin.py
│   └── templates/
│       ├── base.html                #   Layout: nav, sidebar, Tailwind/HTMX/Alpine CDN links
│       ├── dashboard/               #   Full-page templates (overview, entity_detail, etc.)
│       ├── landing/                 #   Public landing page designs (v1-v4)
│       └── partials/                #   HTMX partial templates (alert_card, sparkline, etc.)
│
├── briefings/                       # AI intelligence briefings
│   ├── models.py                    #   WeeklyBriefing, BriefingConfig, EmailConfig
│   ├── generator.py                #   LLM briefing generation pipeline
│   └── email.py                    #   Email delivery
│
└── data/
    └── anomaly_models/              #   Trained Isolation Forest .joblib files (gitignored)
```

**App responsibility rule:** `core` owns domain models. `fetchers` owns data ingestion. `metrics` owns storage, detection, and AI analysis. `scheduler` owns job orchestration. `dashboard` owns HTTP and templates. `briefings` owns LLM generation and email delivery. No circular imports between apps.

---

## 4. Data Model

### Entity-Relationship Overview

```
                              core
                    +-----------------------+
                    |       Vertical        |
                    |  name, slug           |
                    +-----------+-----------+
                                |1
                                |
                                |*
+-----------------+  +---------+-----------+  +-------------------+
|   UserProfile   |  |       Entity        |  |  SavedComparison  |
| user (1:1 User) +--+ name, slug          +--+ entity_a, entity_b|
| watched_entities|* | vertical (FK)       |  | user (FK User)    |
| digest_enabled  |  | is_primary          |  +-------------------+
| timezone        |  | github_org/repo     |       dashboard
+-----------------+  | app_store_id        |
      core           | play_store_id       |
                     | twitter_handle      |
                     | news_keywords (JSON) |
                     | reddit_keywords(JSON)|
                     +--+--+--+--+---------+
                        |  |  |  |
            +-----------+  |  |  +-----------+
            |1             |1 |1             |1
            |*             |* |*             |*
  +---------+--+  +--------+-+  +-----------+-+  +-------------------+
  |MetricSnapshot|  |  Metric  |  |    Alert    |  |    NewsItem       |
  | source       |  | source   |  | alert_type  |  | title, url (uniq) |
  | data (JSON)  |  | key      |  | message     |  | source_name       |
  | captured_at  |  | value    |  | metadata    |  | entities (M2M)    |
  +--------------+  | captured |  | created_at  |  | published_at      |
                    +----------+  +------+------+  | content           |
                         metrics         |         +-------------------+
                                         |*              metrics
                                  +------+------+
                                  |  AlertSeen  |
                                  | user (FK)   |
                                  | alert (FK)  |
                                  | useful (bool|None)
                                  +-------------+
                                     metrics

  +------------------+   +------------------+   +-------------------+
  | CandidateEntity  |   | DeltaThreshold   |   | ThresholdSnapshot |
  | name (unique)    |   | metric_key (uniq)|   | entity (FK, null) |
  | mention_count    |   | threshold_pct    |   | metric_key        |
  | status           |   | lookback_days    |   | effective_threshold|
  | category         |   | use_dynamic      |   | source (static/   |
  | relevance_score  |   +------------------+   |  dynamic/cold)    |
  | promoted_entity  |        metrics            +-------------------+
  |  (FK Entity,null)|                                metrics
  +------------------+
       metrics

  +------------------+   +------------------+
  |      GARun       |   |  CompoundRule    |
  | status           |   | conditions (JSON)|
  | population_size  |   | fitness_score    |
  | generations      |   | precision/recall |
  | best_fitness     |   | f1_score         |
  | rules_produced   |   | is_active        |
  | fitness_history  |   | created_by_run   |
  +--------+---------+   |  (FK GARun,null) |
           |1             +------------------+
           |*                  metrics
  (CompoundRule.created_by_run)

  +------------------+   +------------------+   +-------------------+
  | WeeklyBriefing   |   | BriefingConfig   |   |   EmailConfig     |
  | period_start/end |   | llm_provider     |   | smtp_host/port    |
  | content (md)     |   | ollama_model     |   | smtp_user/pass    |
  | model_used       |   | groq_api_key     |   | use_tls           |
  | status           |   | groq_model       |   | default_from      |
  | entity/alert/    |   | period_days      |   | (singleton, pk=1) |
  |  news counts     |   | (singleton, pk=1)|   +-------------------+
  +------------------+   +------------------+        briefings
       briefings              briefings

  +------------------+   +------------------+
  | FetcherConfig    |   |    BatchRun      |
  | source (unique)  |   | job_id           |
  | interval_hours   |   | triggered_at     |
  | enabled          |   | started/finished |
  +------------------+   | status           |
      scheduler           | entities_total   |
                          | entities_success |
                          | entities_failed  |
                          | error_log (JSON) |
                          +------------------+
                              scheduler
```

### Model Reference

| App | Model | Purpose | Key Fields | Relationships |
|-----|-------|---------|------------|---------------|
| core | **Vertical** | Product category (e.g. "Wallet (HW)") | `name`, `slug` (unique) | Reverse FK from Entity |
| core | **Entity** | Tracked organisation or product | `name`, `slug`, `is_primary`, `github_org`, `github_repo`, `app_store_id`, `play_store_id`, `twitter_handle`, `news_keywords` (JSON), `reddit_keywords` (JSON) | FK to Vertical; reverse FK from MetricSnapshot, Metric, Alert; M2M from NewsItem, UserProfile |
| core | **UserProfile** | Per-user settings and watchlist | `digest_enabled`, `show_all_entities`, `timezone` | OneToOne to User; M2M to Entity (`watched_entities`) |
| metrics | **MetricSnapshot** | Raw JSON payload per fetch cycle (immutable audit trail) | `source`, `data` (JSON), `captured_at` | FK to Entity |
| metrics | **Metric** | Extracted numeric value for time-series queries | `source`, `key` (e.g. `github_stars`), `value` (float), `captured_at` | FK to Entity |
| metrics | **Alert** | Notification fired by detection pipeline | `alert_type` (choices), `message`, `metadata` (JSON), `seen` (deprecated) | FK to Entity; reverse FK from AlertSeen |
| metrics | **DeltaThreshold** | Admin-configurable sensitivity per metric | `metric_key` (unique), `threshold_pct`, `lookback_days`, `use_dynamic_threshold` | None |
| metrics | **ThresholdSnapshot** | Historical record of effective threshold values | `metric_key`, `effective_threshold`, `source` (static/dynamic/cold_start) | FK to Entity (nullable) |
| metrics | **NewsItem** | Deduplicated news article | `title`, `url` (unique), `source_name`, `content`, `published_at` | M2M to Entity |
| metrics | **CandidateEntity** | LLM-extracted competitor candidate | `name` (unique), `mention_count`, `status` (pending/promoted/dismissed), `category`, `relevance_score` | FK to Entity (nullable, for promoted) |
| metrics | **AlertSeen** | Per-user alert dismissal with feedback | `seen_at`, `useful` (True/False/None) | FK to User; FK to Alert; unique together (user, alert) |
| metrics | **GARun** | Genetic algorithm optimisation run metadata | `status`, `population_size`, `generations`, `best_fitness`, `rules_produced`, `fitness_history` (JSON) | Reverse FK from CompoundRule |
| metrics | **CompoundRule** | GA-discovered multi-metric alert rule | `conditions` (JSON), `fitness_score`, `precision`, `recall`, `f1_score`, `is_active` | FK to GARun (nullable) |
| dashboard | **SavedComparison** | User-bookmarked entity pair for head-to-head view | `name` | FK to User; two FKs to Entity |
| briefings | **WeeklyBriefing** | AI-generated intelligence report | `period_start`, `period_end`, `content` (markdown), `model_used`, `status` | None |
| briefings | **BriefingConfig** | Singleton LLM configuration | `llm_provider`, `ollama_model`, `groq_model`, `groq_api_key`, `period_days` | None (singleton, pk=1) |
| briefings | **EmailConfig** | Singleton SMTP settings | `smtp_host`, `smtp_port`, `smtp_user`, `smtp_password`, `use_tls` | None (singleton, pk=1) |
| scheduler | **FetcherConfig** | Per-source job interval and enabled flag | `source` (unique), `interval_hours`, `enabled` | None; post_save signal triggers live rescheduling |
| scheduler | **BatchRun** | Job execution record with per-entity detail | `job_id`, `status`, `entities_total/success/failed`, `error_log` (JSON) | None |

### Design Patterns

**Singleton models.** `BriefingConfig` and `EmailConfig` enforce `pk=1` in their `save()` method and expose a `get()` classmethod that returns or creates the single row. This allows configuration via the Django admin without restarting the server.

**Dual-write denormalisation.** Every fetch produces both a `MetricSnapshot` (the raw JSON payload, kept as an immutable audit trail) and one or more `Metric` rows (flat key/value pairs). Snapshots preserve the full API response; Metrics enable fast time-series queries and delta detection without parsing JSON.

**Per-user alert dismissal.** `AlertSeen` replaces the legacy `Alert.seen` boolean. Each user dismisses independently — one person triaging alerts does not hide them from others. The optional `useful` field (True/False/None) feeds back into the GA optimiser as training signal.

---

## 5. Data Ingestion Pipeline

### BaseFetcher Contract

Every fetcher inherits from `BaseFetcher` (`fetchers/base.py`) and implements:

```
class SomeFetcher(BaseFetcher):
    source_name = "source_id"           # Used as metric key prefix

    def fetch(self) -> dict:            # Hit external API, return raw payload
        ...

    def extract_metrics(self, data: dict) -> dict[str, float]:
        ...                             # Return {metric_key: numeric_value}
```

**Lifecycle:** `run()` calls `fetch()`, then `store(data)` which creates one `MetricSnapshot` and bulk-creates `Metric` rows from `extract_metrics(data)`. All exceptions are caught — a fetcher failure is logged and recorded in `BatchRun.error_log` but never crashes the scheduler or blocks other entities.

### Fetcher Summary

| Source | Class | API / Method | Metrics Extracted | Entity Config Required | Default Interval |
|--------|-------|-------------|-------------------|----------------------|-----------------|
| `github` | `GithubFetcher` | GitHub REST API v3 (token optional) | `github_stars`, `github_forks`, `github_open_issues`, `github_public_repos`, `github_contributors`, `github_latest_release_days_ago`, `github_commits_30d`, `github_commits_90d` | `github_org` | 24h |
| `news` | `NewsFetcher` | Google News RSS (no auth) | `news_article_count` | `news_keywords` non-empty | 2h |
| `appstore` | `AppStoreFetcher` | iTunes Search API (no auth) | `appstore_rating`, `appstore_rating_count`, `appstore_last_update_days_ago` | `app_store_id` | 12h |
| `playstore` | `PlayStoreFetcher` | google-play-scraper (web scraping) | `playstore_rating`, `playstore_rating_count`, `playstore_last_update_days_ago` | `play_store_id` | 12h |
| `twitter` | `TwitterFetcher` | Twitter v2 recent-search (bearer token) | `twitter_mention_count`, `twitter_total_engagement`, `twitter_avg_engagement` | `twitter_handle` + `TWITTER_BEARER_TOKEN` | 1h |
| `reddit` | `RedditFetcher` | Reddit public JSON API (no auth) | `reddit_mention_count` | `reddit_keywords` non-empty | 2h |
| `changelog` | `ChangelogFetcher` | GitHub releases.atom (no auth) | release tag detection | `github_org` + `github_repo` | 12h |

---

## 6. Detection Pipeline

After each fetcher job completes, a chain of detection hooks runs against the newly stored data:

```
 Fetcher completes
      |
      +---> 1. Static Delta Detection ---- compares latest vs. baseline (N days ago)
      |          threshold from DeltaThreshold model
      |          --> METRIC_SPIKE alert
      |
      +---> 2. Dynamic Threshold Detection ---- Isolation Forest anomaly score
      |          trained monthly on historical data
      |          --> METRIC_SPIKE alert (if model exists)
      |
      +---> 3. Release Detection ---- compares two most recent GitHub snapshots
      |          --> NEW_RELEASE alert (tag changed)
      |          --> NEW_REPO alert (repo count increased)
      |
      +---> 4. Compound Rule Detection ---- evaluates GA-optimised multi-metric rules
      |          all conditions in a rule must fire (AND conjunction)
      |          --> COMPOUND_SPIKE alert
      |
      +---> 5. Competitor Detection (news only) ---- LLM extracts entity names
               batches articles within context window budget
               filters by category + relevance score >= 6
               --> CandidateEntity rows
               --> COMPETITOR_NEW alert (after 3+ mentions)
```

### Alert Types

| Alert Type | Trigger | Severity | Example |
|------------|---------|----------|---------|
| `metric_spike` | Metric changes beyond threshold (static or dynamic) | LOW / MEDIUM / HIGH based on delta-to-threshold ratio | "GitHub stars for Jade increased 35% (700 to 945)" |
| `new_release` | GitHub release tag changes between snapshots | N/A | "Jade released v2.1.0 (previous: v2.0.3)" |
| `new_repo` | Public repo count increases | N/A | "Blockstream added a new public repository (15 to 16)" |
| `compound_spike` | All conditions in a GA-optimised rule fire simultaneously | Derived from max individual condition severity | "Compound rule triggered: github_stars >= 20% AND news_article_count >= 50%" |
| `competitor_new` | Untracked entity mentioned 3+ times in news articles | N/A | "New competitor detected: Strike (mentioned in 5 articles)" |

**Severity tiers** (for `metric_spike` and `compound_spike`):
- **LOW:** delta-to-threshold ratio between 1x and 2x
- **MEDIUM:** ratio between 2x and 5x
- **HIGH:** ratio 5x or above

For detailed documentation on each AI-powered detection subsystem, see [docs/AI/](AI/overview.md).

---

## 7. Scheduler

### Scheduled Jobs

All jobs are managed by APScheduler running as a daemon thread inside the Django process. The scheduler starts automatically in `SchedulerConfig.ready()`.

#### Interval Jobs (Data Fetchers)

| Job ID | Fetcher | Default Interval | Entity Filter |
|--------|---------|-----------------|---------------|
| `github_fetcher` | GithubFetcher | 24h | Entities with `github_org` set |
| `news_fetcher` | NewsFetcher | 2h | Entities with `news_keywords` non-empty |
| `appstore_fetcher` | AppStoreFetcher | 12h | Entities with `app_store_id` set |
| `playstore_fetcher` | PlayStoreFetcher | 12h | Entities with `play_store_id` set |
| `twitter_fetcher` | TwitterFetcher | 1h | Entities with `twitter_handle` set |
| `reddit_fetcher` | RedditFetcher | 2h | Entities with `reddit_keywords` non-empty |
| `changelog_fetcher` | ChangelogFetcher | 12h | Entities with `github_org` + `github_repo` set |

#### Cron Jobs (Intelligence & Maintenance)

| Job ID | Schedule | Description |
|--------|----------|-------------|
| `weekly_digest` | Sunday 09:00 UTC | Builds and emails weekly alert digest per user (scoped to watchlist) |
| `monthly_anomaly_training` | 1st of month, 02:00 UTC | Retrains Isolation Forest models for dynamic thresholds |
| `monthly_compound_optimization` | 1st of month, 03:00 UTC | GA-optimises compound alert rules from AlertSeen feedback |
| `weekly_briefing` | Sunday 10:00 UTC | Generates AI intelligence briefing via Ollama/Groq |

### Live Rescheduling

`FetcherConfig` has a `post_save` signal that calls `reschedule_job()`. When an admin changes a fetcher's interval or disables it through the Django admin, the scheduler updates the in-memory job immediately — no restart required.

### Execution Tracking

Every job execution creates a `BatchRun` record with:
- **Status:** `running` / `success` / `partial` / `failed` — derived from per-entity outcomes
- **Per-entity detail:** `entities_total`, `entities_success`, `entities_failed`, plus `error_log` (JSON dict mapping entity slug to error message)
- **Duration:** `started_at` and `finished_at` for performance monitoring

The scheduler dashboard at `/scheduler/` displays BatchRun history.

**Misfire handling:** `misfire_grace_time=3600` (1-hour window) with `coalesce=True` — if the server was down and multiple intervals were missed, only one catch-up run fires.

---

## 8. Frontend Architecture

### Philosophy

The frontend uses server-rendered Django templates with three CDN-loaded libraries for interactivity:

- **HTMX** — handles all dynamic updates. Views return HTML partials, HTMX swaps them into the DOM. No client-side state management, no fetch/setState/render cycles.
- **Alpine.js** — handles client-only interactions: dropdowns, toggles, tab switches, filter panels. Never used for data fetching.
- **Chart.js** — renders sparklines, radar charts, bar charts, and trend lines. Data is passed as inline JSON in the template context.

No build step exists. No npm, no webpack, no PostCSS. All JS/CSS loads from CDN.

### HTMX Request/Response Cycle

```
 Browser                          Django
    |                               |
    |  hx-get="/partials/alerts/"   |
    |  hx-trigger="every 30s"       |
    +------------------------------>|
    |                               |  view renders partial template
    |    <div id="alert-list">      |  (just the HTML fragment, no base layout)
    |      ... alert cards ...      |
    |    </div>                     |
    |<------------------------------+
    |                               |
    |  HTMX swaps innerHTML of      |
    |  target element               |
    |                               |
```

### Template Hierarchy

```
base.html                          # Nav, sidebar, CDN links, block content
  |
  +-- dashboard/overview.html      # Full pages extend base.html
  +-- dashboard/entity_detail.html
  +-- dashboard/alerts.html
  +-- ...
  |
  +-- partials/alert_card.html     # Standalone HTML fragments returned by HTMX endpoints
  +-- partials/sparkline.html      # No base.html inheritance — just the fragment
  +-- ...
```

### URL Routes

#### Public Pages (No Authentication)

| URL | View | Description |
|-----|------|-------------|
| `/` | `landing` | Public landing page (redirects to the dashboard when logged in) |

All landing views redirect authenticated users to `/dashboard/`.

#### Full-Page Views (Authentication Required)

| URL | View | Description |
|-----|------|-------------|
| `/dashboard/` | `main_dashboard` | Executive cockpit: KPIs, rankings, radar, news volume, trend charts, top movers, entity table |
| `/landscape/` | `landscape_view` | Force-directed network graph (Cytoscape.js) of entity relationships |
| `/vertical/<slug>/` | `vertical_detail` | Entities within a vertical, with comparison metrics |
| `/entity/<slug>/` | `entity_detail` | Single entity: all metrics, sparklines, news, alerts |
| `/compare/` | `head_to_head` | Side-by-side comparison of two entities (radar + trends) |
| `/feed/` | `feed` | Unified feed: news, releases, alerts (filterable by entity) |
| `/alerts/` | `alerts` | Alert feed with source/entity filters, mark-seen, dismiss |
| `/candidates/` | `candidate_list` | LLM-detected competitor candidates: promote, dismiss |
| `/ai/` | `ai_observatory` | AI subsystem dashboard: anomaly models, GA runs, alert volume |
| `/scheduler/` | `scheduler_status` | Scheduler health: BatchRun history, job status |
| `/briefing/` | `briefing` | AI briefings: view, generate on-demand, configure settings |
| `/profile/settings/` | `profile_settings` | User preferences: watchlist, timezone, digest |

#### HTMX Partial Endpoints

| URL | Returns |
|-----|---------|
| `/partials/entity-table/` | Sortable entity overview table |
| `/partials/alerts/` | Alert card list (supports HTMX polling) |
| `/partials/comparison/` | Head-to-head comparison panel |
| `/partials/sparkline/<entity>/<metric>/` | Single sparkline chart for one metric |
| `/partials/briefing-status/<pk>/` | Briefing generation progress |

#### Chart JSON Endpoints

| URL | Returns |
|-----|---------|
| `/partials/exec-vertical-bars/` | Vertical bar chart data |
| `/partials/exec-radar/` | Radar chart data |
| `/partials/exec-severity/` | Alert severity distribution |
| `/partials/exec-news-volume/` | News volume over time |
| `/partials/exec-trend/` | Metric trend data |
| `/partials/exec-movers/` | Top movers data |
| `/partials/ranking-chart/` | Entity ranking chart data |
| `/partials/news-histogram/<entity>/` | Per-entity news histogram |
| `/partials/dual-trend/` | Dual-axis trend comparison |

#### Action Endpoints (POST)

| URL | Action |
|-----|--------|
| `/alerts/<pk>/mark-seen/` | Mark single alert as seen (creates AlertSeen) |
| `/alerts/<pk>/undismiss/` | Remove AlertSeen record |
| `/alerts/mark-all-seen/` | Mark all visible alerts as seen |
| `/alerts/entity/<slug>/mark-all-seen/` | Mark all alerts for one entity as seen |
| `/candidates/<pk>/promote/` | Promote candidate to tracked Entity |
| `/candidates/<pk>/dismiss/` | Dismiss candidate permanently |
| `/candidates/dismiss-all/` | Dismiss all pending candidates |
| `/entity/<slug>/watch/` | Toggle entity in user's watchlist |
| `/toggle-scope/` | Toggle between watchlist-only and all-entities view |
| `/comparisons/save/` | Save a head-to-head comparison |
| `/briefing/generate/` | Trigger on-demand briefing generation |
| `/briefing/settings/` | Update briefing LLM configuration |

---

## 9. Authentication and Security

### Current Measures

| Area | Implementation |
|------|---------------|
| **Authentication** | Django session-based auth. `@login_required` on all dashboard views. Built-in login/logout/password-change views at `/accounts/`. |
| **CSRF** | Django `CsrfViewMiddleware` enabled. All POST forms include `{% csrf_token %}`. HTMX configured to send CSRF header. |
| **XSS** | Django template auto-escaping enabled by default. Scraped article content sanitised with `nh3` before storage. |
| **Clickjacking** | `X-Frame-Options: DENY` via `XFrameOptionsMiddleware`. |
| **Input validation** | Entity keyword lists validated by `validate_keyword_list()`: max 10 keywords, max 100 chars each. |
| **Secret management** | `django-environ` loads secrets from `.env` file (gitignored). `.env.example` documents required variables without values. |
| **Static files** | WhiteNoise serves and compresses static files. No directory listing. |
| **SQL injection** | Django ORM parameterised queries throughout. No raw SQL. |
| **LLM prompt injection** | Tracked entity names are injected into LLM system prompts as exclusion lists; user-generated content is never placed in the system prompt. |

### Deferred for Production

| Area | What's Missing | Priority |
|------|---------------|----------|
| HTTPS enforcement | No `SECURE_SSL_REDIRECT` or HSTS headers. Requires reverse proxy (nginx/Caddy). | High |
| Content Security Policy | No CSP headers. CDN scripts would need whitelisting. | High |
| Rate limiting | No request-rate throttling on login or action endpoints. | Medium |
| Password complexity | Default Django password validators only. | Medium |
| Audit logging | No record of who changed what in admin. | Low |
| Secret rotation | API keys stored in `.env` with no rotation mechanism. | Low |
| API authentication | No REST API exists; if DRF is added, token/JWT auth will be needed. | Future |

---

## 10. AI Subsystems

The system includes four AI-powered capabilities, all of which degrade gracefully if their dependencies (Ollama, Groq, trained models) are unavailable.

| Subsystem | Technique | Schedule | Detailed Documentation |
|-----------|----------|----------|----------------------|
| **Anomaly Detection** | Isolation Forest (scikit-learn) per (entity, metric_key) | Monthly retraining (1st of month, 02:00 UTC) | [docs/AI/anomaly_detection.md](AI/anomaly_detection.md) |
| **Compound Alert Rules** | Genetic Algorithm optimisation with F-beta 0.5 fitness | Monthly optimisation (1st of month, 03:00 UTC) | [docs/AI/compound_alert_rules.md](AI/compound_alert_rules.md) |
| **Competitor Detection** | LLM entity extraction from news article content | Every news fetcher cycle (2h default) | [docs/AI/competitor_detection.md](AI/competitor_detection.md) |
| **Intelligence Briefings** | LLM summarisation of alerts, metrics, and news | Weekly (Sunday 10:00 UTC) + on-demand | [docs/AI/llm_briefing_system.md](AI/llm_briefing_system.md) |

**Dual LLM backend:** All LLM features share a common `LLMClient` that tries Ollama (local) first and falls back to Groq (cloud). Provider preference is configurable via the `BriefingConfig` singleton in the Django admin. If neither backend is reachable, the LLM-dependent feature silently skips and logs a warning.

For a non-technical overview of these subsystems, see [docs/AI/overview.md](AI/overview.md).

---

## 11. Scope Trade-offs

This project was built as a 3-week MVP. The original brief recommended a specific tech stack and required a minimum set of features. Several decisions deviated from the brief to ship faster with a single developer.

### Architectural Deviations

| Original Brief | What We Built | Why |
|---------------------------|--------------|-----|
| React + Tailwind CSS + Recharts/D3.js | Django templates + HTMX + Tailwind (CDN) + Chart.js (CDN) | Internal tool, read-heavy workload. No build step, no client-side routing, no API serialisation layer. HTMX delivers the same UX with a fraction of the complexity. Full rationale in [Section 11.1 Architectural Deviations](#11-scope-trade-offs). |
| Node.js + Express or Python + FastAPI | Django 6.x monolith | Django's ORM, admin, auth, and template engine eliminate boilerplate. Scheduler and views share the same ORM — no API calls needed between components. |
| Recharts or D3.js for charts | Chart.js 4.x (CDN) | Works natively with server-rendered templates. Data passed as inline JSON, no npm dependency, no bundler. |
| 3+ fetchers minimum | 7 fetchers | GitHub (required) + News, App Store (iOS), Play Store (Android), Twitter/X, Reddit, Changelog. |

### Beyond the Brief (Bonus Features)

These features were not required by the original brief but were implemented to demonstrate capability:

| Feature | What It Does |
|---------|-------------|
| **Anomaly detection** | Isolation Forest per (entity, metric_key) with monthly retraining and cold-start LLM suggestions |
| **Compound alert rules** | Genetic Algorithm discovers multi-metric rules from user feedback (AlertSeen) |
| **AI briefings** | Dual LLM backend (Ollama + Groq) generates weekly intelligence reports |
| **LLM competitor detection** | Automatic entity extraction from news with category classification and relevance scoring |
| **Scheduler dashboard** | Real-time view of BatchRun history and job health at `/scheduler/` |
| **AI Observatory** | Dashboard for anomaly models, GA runs, and alert volume at `/ai/` |
| **Test suite** | pytest + factory_boy + responses mocking across all apps |

---

## 12. Current Limitations

| Limitation | Impact | Mitigation |
|------------|--------|------------|
| **SQLite database** | No concurrent write support. Single-writer lock can block under load. | Sufficient for internal tool with single-digit concurrent users. PostgreSQL migration is a settings change (see [Roadmap](#14-production-upgrade-roadmap)). |
| **No async / Celery** | Fetcher jobs run in-process via APScheduler daemon thread. Long-running fetchers block that thread. | 7 fetcher jobs on hourly/daily intervals complete within seconds each. Adequate at current scale. |
| **No REST API** | All rendering is server-side. No way for external tools to consume data programmatically. | DRF can be added alongside existing views without breaking anything ([migration path in SPEC.md](../SPEC.md)). |
| **No caching layer** | No Redis/Memcached. Every page load queries the database directly. | Acceptable at current data volume (~31 entities, ~18 metric keys). |
| **No CI/CD pipeline** | No automated linting, testing, or deployment on push. | Tests run locally via `uv run pytest src/`. |
| **Twitter free tier** | 1 request per 15 minutes. Cannot increase without paid API access. | TwitterFetcher returns empty dict on 429 (rate limit) without crashing. |
| **Play Store scraping** | No official API. Depends on `google-play-scraper` library which may break if Google changes their HTML. | PlayStoreFetcher handles `NotFoundError` gracefully. |
| **CDN dependency** | HTMX, Alpine.js, Tailwind CSS, and Chart.js all load from CDN. No offline fallback. | Could vendor the files into `static/` if offline support is needed. |
| **Email via SMTP** | No transactional email service (SendGrid, Mailgun). Direct SMTP may have deliverability issues. | EmailConfig singleton allows SMTP settings to be changed without code deployment. |

---

## 13. Production Upgrade Roadmap

Prioritised from highest to lowest impact:

1. **PostgreSQL migration** — Unlock concurrent writes, full-text search, and JSONB operators. Django makes this a settings change plus `dumpdata`/`loaddata` for data migration. Eliminates the single-writer SQLite bottleneck.

2. **Celery + Redis** — Move fetcher jobs to async Celery tasks, decouple the scheduler from the web process. Enables horizontal scaling (multiple workers) and prevents long-running fetchers from blocking the scheduler thread.

3. **REST API (DRF)** — Add read-only `ModelViewSet` endpoints for Entity, Metric, Alert, and NewsItem at `/api/v1/`. Enables external consumers, mobile apps, or a future React frontend. Existing HTMX views stay live during migration. See [SPEC.md](../SPEC.md) for the step-by-step plan.

4. **Caching (Redis / Memcached)** — Cache entity lists, metric aggregations, and dashboard page fragments. Reduces database load as data volume grows.

5. **CI/CD (GitHub Actions)** — Automated pipeline: lint with `ruff`, test with `pytest`, build Docker image, deploy. Run on every push and PR.

6. **HTTPS + CSP headers** — Deploy behind a reverse proxy (nginx or Caddy) with TLS termination. Add Content Security Policy headers with CDN domains whitelisted.

7. **Rate limiting** — Add `django-ratelimit` on login, alert actions, and briefing generation endpoints. Prevents brute-force and abuse.

8. **Monitoring** — Sentry for error tracking, Prometheus + Grafana for scheduler health, fetcher success rates, and alert volume trends.

---

## 14. How to Extend

### Adding a New Fetcher

1. Create `src/fetchers/newname.py` inheriting from `BaseFetcher`
2. Set `source_name = "newname"` (used as metric key prefix)
3. Implement `fetch()` (hit the API, return raw dict) and `extract_metrics()` (return `{key: float}`)
4. Add the required entity config field to `entities.json` and `Entity` model (if new identifier needed)
5. Create `DeltaThreshold` rows for each new metric key (via admin or migration)
6. Register the job in `scheduler/jobs.py`: add to `SOURCE_TO_JOB` map and create a `run_newname_fetcher()` function
7. Add a `FetcherConfig` row with `source="newname"` and desired interval
8. Write tests: mock the API with `responses`, verify MetricSnapshot and Metric creation

See [.claude/skills/fetchers.md](../.claude/skills/fetchers.md) for the full pattern.

### Adding a New Dashboard Page

1. Create the view function in `src/dashboard/views.py` (decorated with `@login_required`)
2. Add the URL pattern in `src/dashboard/urls.py`
3. Create the template in `src/dashboard/templates/dashboard/newpage.html` extending `base.html`
4. Add a navigation link in `base.html`
5. If the page uses HTMX partials, create templates in `partials/` and add corresponding partial endpoints

See [.claude/skills/htmx-patterns.md](../.claude/skills/htmx-patterns.md) for HTMX patterns.

### Adding a New Model Field

1. Add the field in the relevant `models.py`
2. Run `python manage.py makemigrations` — never hand-write migration files
3. Run `python manage.py migrate`
4. Update the factory in the corresponding `tests/factories.py`
5. Update any affected views, templates, or admin classes

### Adding a New Management Command

1. Create `src/<app>/management/commands/commandname.py`
2. Inherit from `BaseCommand`, implement `add_arguments()` and `handle()`
3. Document in [docs/management_commands.md](management_commands.md)

See existing commands (e.g. `seed.py`, `generate_briefing.py`) for the pattern.

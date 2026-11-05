# SPEC.md — Implementation Status

What was implemented and what was not, relative to the original brief.

---

## What Was Implemented

### Layer 1: Data Ingestion (7 fetchers)

| Fetcher | Status | Metrics |
|---------|--------|---------|
| **GitHub** (required) | Done | stars, forks, contributors, commit frequency 30/90d, open issues, latest release date, public repo count |
| **News** | Done | Google News RSS, keyword-based search, deduplication by URL, entity tagging, article count |
| **App Store (iOS)** | Done | iTunes Search API — rating, rating count, days since update, version in raw snapshot |
| **Social: Twitter/X** | Done | Twitter v2 API search — mention count, total engagement, avg engagement |
| **Social: Reddit** | Done | Reddit public API — mention count (keyword search, 500-post safety cap) |
| **App Store (Android)** | Done | google-play-scraper — rating, rating count, days since update |
| **Changelog** | Done | GitHub releases.atom RSS — releases total, days since latest release, release tag detection |

### Layer 2: Storage and Processing

| Feature | Status | Notes |
|---------|--------|-------|
| **SQLite + production schema** | Done | Indexed models, foreign keys, proper migrations |
| **Scheduled refresh** | Done | APScheduler with per-source configurable intervals via FetcherConfig |
| **Delta detection** | Done | Static thresholds + dynamic (Isolation Forest anomaly detection) |
| **New competitor candidate** | Done | Regex-based name extraction from news titles, CandidateEntity model, COMPETITOR_NEW alerts |
| **New product or release** | Done | NEW_RELEASE (GitHub API + changelog RSS) and NEW_REPO alert types |

### Layer 3: Frontend Dashboard

| Feature | Status | Notes |
|---------|--------|-------|
| **Entity overview** | Done | Sortable table with configurable metrics, vertical filter, name search |
| **Drill-down navigation** | Done | Overview > Vertical detail > Entity detail with breadcrumbs on all pages |
| **Head-to-head view** | Done | Radar charts, side-by-side metrics, trend comparison, saved comparisons |
| **Metrics panel with sparklines** | Done | Chart.js sparklines with 30d/90d/All range selector, delta badges |
| **Unified feed** | Done | Three-column layout (News / Releases / Alerts), filterable by entity |
| **Alert feed** | Done | Filterable by source and entity, mark seen/dismiss, HTMX polling, competitor candidates page |

### Beyond the Brief (Bonus Features)

| Feature | Notes |
|---------|-------|
| **Anomaly detection** | Isolation Forest per (entity, metric_key), cold-start LLM suggestions, monthly retraining |
| **Compound alert rules** | Genetic Algorithm optimizer with AlertSeen-based learning (F-beta 0.5 fitness) |
| **AI briefings** | Dual LLM backend (Ollama local + Groq cloud fallback), weekly schedule, on-demand generation |
| **News Intelligence** | LLM-powered sentiment classification (positive/neutral/negative/mixed), signal type detection (product launch, partnership, funding, vulnerability, etc.), per-article strategic summaries and key quotes, Plotly sentiment heatmap, breakdown stats, on-demand executive takeaways. Ollama-only by default (`NEWS_ENRICHMENT_OLLAMA_ONLY`) to avoid burning cloud API tokens. Runs as post-fetch hook on the news scheduler. Dedicated `/news/` page with Alpine.js filters. |
| **Momentum tracking** | Z-score composite speed/acceleration per entity, scatter quadrant map, filterable time-series, entity detail badge |
| **Test suite** | pytest + factory_boy + responses mocking for all fetchers and core logic |

---

## Out-of-Scope Features

Features not built in the current release, with implementation notes for future contributors.

---

### CoinGecko / Mempool.space Integration

**Description:** Add Bitcoin network and token market metrics (price, volume, hash rate) to the dashboard.

**Why not built:** Secondary to core competitive metrics; adds scope beyond the original brief.

**Data requirements:** CoinGecko API (free, no auth); Mempool.space API (free, no auth).

**Implementation notes:**
- `CoinGeckoFetcher`: `GET https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd`
- Metrics: `coingecko_price_usd`, `coingecko_market_cap`
- `MempoolFetcher`: `GET https://mempool.space/api/v1/mining/hashrate/3m`
- Metrics: `mempool_hashrate_3m`, `mempool_fee_rate_avg`
- Both APIs are free, no keys required; add to scheduler with 1-hour interval

---

### Job Changes Tracking

**Description:** Track hiring activity (job postings, LinkedIn changes) for tracked entities as part of the unified feed.

**Why not built:** No free public API exists for job data. LinkedIn's API does not expose job listings without a paid partnership. Indeed and Glassdoor APIs are deprecated.

**Data requirements:** LinkedIn company job postings or job board RSS feeds.

**Implementation notes:**
- Some companies publish RSS job feeds (e.g., `https://company.com/careers/feed`). A `JobFetcher` using `feedparser` could scrape these on a per-entity basis if feed URLs are added to `entities.json`.
- Alternative: scrape `linkedin.com/jobs/search?company={id}` via unofficial methods (fragile, ToS risk).
- Recommended approach: add an optional `careers_rss_url` field to `entities.json`; implement `JobFetcher` only for entities where the field is populated.

---

### React Frontend (Future Migration Path)

**Description:** Replace HTMX templates with a standalone React SPA backed by a DRF API.

**Why not built:** Django monolith with HTMX delivers the same UX with far less complexity for this prototype.

**Data requirements:** No new data; existing models become API resources.

**Implementation notes:**
1. `pip install djangorestframework drf-spectacular`
2. Create `api/` app with read-only `ModelViewSet` for `Entity`, `Metric`, `Alert`, `NewsItem`
3. Expose `/api/v1/` alongside existing template views (zero breakage)
4. React app in `frontend/` consumes `/api/v1/` — can be deployed separately or served by Django
5. Existing HTMX views can stay live during migration; swap page-by-page

# Video Demonstration Guide

**Audience:** Teams evaluating the platform
**Goal:** Let them evaluate the platform in ~10 minutes and decide whether to clone and extend it
**Target length:** 9–10 minutes
**Format:** Screen recording with voice-over

---

## Before You Hit Record

Run through this checklist. A failed demo is worse than no demo.

- [ ] `uv run python src/manage.py seed_demo` completed with no errors (or data is already seeded)
- [ ] Server is running: `uv run python src/manage.py runserver`
- [ ] Browser is open at `http://localhost:8000`, logged out
- [ ] Browser zoom at 100%, notifications silenced, other tabs closed
- [ ] **LLM check (pick one):**
  - Ollama: `ollama serve` is running and `ollama pull llama3.2` completed
  - Groq: Groq API key is set in `/briefing/settings/` — test it with one manual generation before recording
- [ ] A weekly briefing already exists and is status `READY` (so you can show it without waiting for generation — you'll demo a live generation separately)
- [ ] At least one HIGH-severity alert exists in the alerts page
- [ ] Competitor Candidates page has at least one PENDING entry

**Fallback if Ollama is down:** Go to `/briefing/settings/`, switch provider to Groq, enter your API key. Groq generation is fast (~10 seconds).

**Fallback if seed data looks thin:** Re-run `uv run python src/manage.py seed_demo_alerts` and `seed_demo_metrics`.

---

## Segment 0 — Hook (0:30)

**What you want them to feel:** "Yes, this is exactly the problem I have."

**What to say:**
> Blockstream competes across 7 verticals — hardware wallets, software wallets, sidechains, custody, satellite, tooling, research. Tracking 37 competitors manually means opening 30 GitHub tabs, googling news every week, and still missing things. This platform does it automatically: it pulls live data from GitHub, app stores, news feeds, and Reddit, detects when competitors move, and generates AI-powered briefings. Let me show you how it works.

---

## Segment 1 — Setup and Launch (1:00)

**What you want them to feel:** "This is actually easy to run."

**Steps:**
1. Show your terminal. Type the commands one by one:
   ```bash
   git clone https://github.com/CarloCogni/Competitive-Intelligence-Platform.git
   cd Competitive-Intelligence-Platform
   uv sync
   cp .env.example .env
   uv run python src/manage.py migrate
   uv run python src/manage.py seed
   uv run python src/manage.py seed_demo
   uv run python src/manage.py runserver
   ```
2. While it runs, say:

> No Redis. No Celery. No Postgres. Just Python. SQLite handles persistence, APScheduler runs jobs inside the same process. The whole thing is a single `runserver` command.

3. Open `http://localhost:8000` — the landing page appears.

**Transition:** "Let me show you a few design options before we log in."

---

## Segment 2 — Landing Pages (0:30)

**What you want them to feel:** "This was built with real craft, not a starter template."

**Steps:**
1. You're on the default landing (`/`) — V4, Blockstream-branded dark design with animated network nodes.
2. Briefly navigate: `/landing/v1/` (Satoshi Terminal, monospace typewriter), `/landing/v2/` (glassmorphic), `/landing/v3/` (network intelligence).
3. Say:

> Four design variants ship out of the box — trivial to extend or replace by dropping a new template. All four route to the same login. Let's go in.

4. Click **Log In**. Credentials: `batman` / `the hero we deserve`.

**Transition:** "This is the executive dashboard — the first thing you see every morning."

---

## Segment 3 — Executive Dashboard (2:30)

**What you want them to feel:** "This is actionable intelligence, not just a table of numbers."

### 3a. KPI Cards (0:20)
- Point at the top row: entities tracked, unseen HIGH/MEDIUM/LOW alerts, news articles this week, releases this week.
- Say: "At a glance — how many things are moving right now, and how serious they are."

### 3b. Charts Section (0:40)
- **Stacked Bar:** "Blockstream products versus competitors per vertical. Change the metric key in the dropdown — stars, forks, commits — chart updates instantly via HTMX, no page reload."
- **Radar Chart:** "Normalized comparison: avg Blockstream across five metrics vs avg competitor. You can see at a glance where you lead and where you don't."
- Briefly point at **News Volume** area chart and **Severity Donut**.

### 3c. Competitive Trend Wall (0:20)
- Six sparklines: GitHub stars, forks, commits 30d, Twitter engagement, Reddit mentions, news volume.
- "These are 30-day time series. Data updates daily across all 37 entities automatically."

### 3d. Top Movers + Entity Table (0:30)
- **Top Movers:** "Biggest percentage changes in the last 7 days. These are the entities that actually moved — not just noisy absolute counts."
- Scroll the entity overview table. Sort by `github_stars`. Say: "Sortable by any metric. Full competitive matrix in one view."

### 3e. Entity Detail (0:40)
- Click on **Ledger** (or any competitor). Land on entity detail.
- Show: 30+ metric sparklines, recent news items, recent alerts.
- Say: "Every tracked entity gets this drill-down automatically — no manual setup per company."
- Navigate to Head-to-Head: compare **Blockstream Jade** vs **Ledger**.
- Show radar overlay + "Jade leads on X metrics, Ledger leads on Y."
- Click **Save Comparison**. "Comparisons are saved per user — useful for recurring analyst workflows."

**Transition:** "Let me show you the competitive landscape from a different angle."

---

## Segment 4 — Landscape and Feed (1:00)

**What you want them to feel:** "This is a real intelligence tool, not a spreadsheet with charts."

### 4a. Landscape Graph (0:40)
- Navigate to `/landscape/`.
- A force-directed graph appears: 37 nodes, colored by vertical (7-color palette), node size = GitHub stars, orange border = Blockstream products.
- Say:

> Each node is a tracked company. Colors are market verticals. Size is GitHub stars — so you immediately see who the GitHub-dominant players are. Orange borders are Blockstream's own products. Edges connect competitors within the same vertical. You can see cluster density: custody is dense and contested, satellite is sparse. This is your competitive map, live.

### 4b. Unified Feed (0:20)
- Navigate to `/feed/`.
- Three columns: **News** | **Releases** | **Alerts**.
- Filter by entity using the dropdown. Say: "Everything in one scrollable place. News articles entity-tagged, GitHub release events, threshold alerts. Filter to any entity instantly."

**Transition:** "Now the part I think is most interesting — the AI layer. This wasn't in the assignment brief. I built it anyway."

---

## Segment 5 — AI Systems (3:00)

**What you want them to feel:** "This engineer understands how to build AI systems that actually do something useful, not just call an LLM and hope."

### 5a. Alerts and the Feedback Loop (0:30)
- Navigate to `/alerts/`.
- Filter by severity **HIGH**. Show a METRIC_SPIKE alert card.
- Mark it as **seen**, then click **Useful** (or **Not Useful**).
- Say:

> This feedback is not cosmetic. Every "useful" or "not useful" click becomes a labeled training example. The Genetic Algorithm reads these labels to optimize its rules. The system gets smarter the more you use it.

### 5b. AI Weekly Briefing (1:00)
- Navigate to `/briefing/`.
- Show the latest READY briefing. It has four sections: **Executive Summary**, **Key Threats**, **Opportunities**, **Watch List**.
- Say:

> This is synthesized from the last 7 days of alerts, metric deltas, and news articles. Not a summary of raw data — an analyst-style brief that tells you what matters and what to watch.

- Navigate to `/briefing/settings/`.
- Show the **LLM Provider** dropdown: Auto / Ollama-only / Groq-only.
- Say:

> Two backends. Ollama runs locally — your data never leaves your machine. Groq is a free cloud API for when you're on a VPS or don't want to run a local model. Auto mode probes Ollama first and falls back to Groq silently.

- Back on `/briefing/`, click **Generate Now**.
- Watch the status partial poll: PENDING → GENERATING → READY.
- Say: "The generation runs in a background job. The page polls via HTMX every 2 seconds — no WebSocket, no client-side state management."

### 5c. AI Observatory (1:00)
- Navigate to `/ai/`.
- **Anomaly Detection section:**
  - Show model count, entities covered, metrics covered.
  - Say:

    > There's an Isolation Forest model per entity-metric pair. Trained monthly on historical data. No hardcoded threshold for 'what counts as a spike' — each company has its own baseline. Ledger's GitHub activity looks different from a small research lab's. The model learns that.

  - Show the **Severity Timeline** chart and **Model Coverage** bar chart.

- **Genetic Algorithm section:**
  - Show GA run history table: population size, generations, best fitness, rules produced.
  - Show **Fitness Convergence** chart (generation # vs best fitness for the most recent run).
  - Say:

    > The GA discovers multi-condition alert rules — things like "github_stars up 5% AND news_article_count up 10% in the same period." It evolves a population of candidate rules, evaluates each one against your labeled alert feedback (the useful/not useful clicks), and surfaces the rules with the best precision and recall. These rules fire COMPOUND_SPIKE alerts. The system is discovering alert patterns you didn't write by hand.

  - Show the **Threshold Evolution** chart: select a metric and entity, watch the threshold value change over time.

### 5d. Competitor Candidates (0:30)
- Navigate to `/candidates/`.
- Show a PENDING candidate: name, mention count, relevance score (1-10), LLM-generated reason, source URLs.
- Say:

> Every news article ingested is parsed by an LLM for company mentions. Names are aggregated by frequency. When a name crosses the mention threshold, it surfaces here with a relevance score and the LLM's reasoning. Click Promote — it becomes a fully tracked entity with auto-populated search keywords.

- Click **Promote** on one candidate.

**Transition:** "One last thing — everything you just saw is configurable without touching code."

---

## Segment 6 — Admin and Live Configuration (0:30)

**What you want them to feel:** "This is actually operable. No engineer needed to adjust it."

- Navigate to `/admin/`.
- **FetcherConfig:** Find the GitHub fetcher. Change interval from `24` hours to `6`. Save.
  - Say: "Reschedules the APScheduler job immediately. No server restart."
- **DeltaThreshold:** Find `github_stars`. Toggle `use_dynamic_threshold` on/off.
  - Say: "Switch per-metric between static percentage thresholds and Isolation Forest anomaly detection."
- **CompoundRule:** Show the list. Toggle `is_active` on a rule.
  - Say: "Enable or disable GA-discovered rules live."
- Say: "Users, watchlists, email digest config, SMTP settings, Groq API key — all managed here."

---

## Segment 7 — Outro (0:30)

**What you want them to feel:** "I want to pull this down and try it."

**What to say:**
> To summarize: 37 entities across 7 verticals, 7 independent data fetchers, 446 tests at 70% coverage. Three AI systems built beyond the assignment scope — anomaly detection with Isolation Forest, compound rule discovery with a Genetic Algorithm, and LLM-powered competitive briefings with local or cloud inference. No cloud dependencies, no vendor lock-in. Pull it from GitHub, swap in your own `entities.json`, and you have a competitive intelligence platform for any industry. Link is in the README.

---

## What to Avoid

| Trap | Why | Fix |
|------|-----|-----|
| Getting stuck in admin | Admin looks like any Django admin — not impressive | Keep it to 30 seconds, move fast |
| Showing an empty state | Empty charts destroy credibility | Always `seed_demo` before recording |
| Demoing LLM generation without knowing how long it takes | If Ollama is slow, you'll sit in silence for 2 minutes | Pre-test generation time; use Groq if Ollama is slow |
| Explaining code | This is a demo, not a code review | Stay in the browser the whole time |
| Over-explaining the GA | The fitness convergence chart speaks for itself | Say one sentence, show the chart, move on |
| Reading the briefing aloud | It's long | Say what the sections are, scroll, move on |

---

## URL Quick Reference

| Page | URL |
|------|-----|
| Home (Landing V4) | `/` |
| Login | `/accounts/login/` |
| Dashboard | `/dashboard/` |
| Landscape Graph | `/landscape/` |
| Unified Feed | `/feed/` |
| Alerts | `/alerts/` |
| Head-to-Head | `/compare/` |
| Competitor Candidates | `/candidates/` |
| Scheduler Health | `/scheduler/` |
| AI Briefing | `/briefing/` |
| Briefing Settings | `/briefing/settings/` |
| AI Observatory | `/ai/` |
| Profile / Watchlist | `/profile/settings/` |
| Admin | `/admin/` |

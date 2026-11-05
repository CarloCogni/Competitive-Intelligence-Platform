# Competitor Candidate Detection (LLM-First)

## AI Technique

**LLM-first entity extraction with article scraping.** News articles are scraped for body content, then batches of titles + content excerpts are sent to an LLM (Ollama local, Groq cloud fallback) that extracts competitor entities with structured metadata: category, relevance score (1-10), and reasoning. Only entities in competitive categories with relevance >= 6 are stored as candidates. No regex pre-filtering — the LLM is the sole extraction engine.

## The Problem

The dashboard tracks a fixed set of competitors loaded from `entities.json`. But the competitive landscape is not static — new wallets, protocols, exchanges, and infrastructure projects appear regularly. Manually scanning news articles for untracked names does not scale across 30+ entities and hundreds of articles per week.

An earlier approach used regex extraction (capitalized multi-word sequences) followed by LLM filtering. This produced too much noise: sentence fragments, media outlets, already-tracked entities, and generic terms all passed through because the LLM saw names stripped of their article context.

## How It Works

The detection runs automatically at the end of every news fetch cycle (every 2 hours by default). It has four stages:

### 1. Article Scraping

All `NewsItem` records from the last 24 hours are processed. For each article, the system attempts to scrape the full article body using `trafilatura` (a Python library for web content extraction). Results are cached on the `NewsItem.content` field so articles are only scraped once.

- **Success**: Article text is stored (up to 5,000 characters) and the first 500 words are used as context for the LLM.
- **Failure**: `content_scraped_at` is set with empty content, preventing retries on dead URLs.
- **Already scraped**: Cached content is returned immediately.

### 2. Context-Window-Aware Batching

Articles (title + content excerpt) are grouped into batches sized to fit within the active LLM's context window:

- **Ollama** (e.g., Llama 3.1 8B, 128k context): ~80,000 token budget
- **Groq**: ~25,000 token budget (conservative default)

Token estimation uses a simple heuristic (~1 token per 4 characters). Each batch includes as many articles as fit within the budget minus system prompt overhead.

### 3. LLM Entity Extraction

Each batch is sent to the LLM with a structured prompt that includes:

- The full list of currently tracked entities (so the LLM knows what to exclude)
- All article titles and content excerpts in the batch
- Instructions to extract competitor entities with structured JSON output

For each extracted entity, the LLM provides:
- **name**: Canonical company/product name
- **category**: One of `competitor`, `exchange`, `wallet`, `protocol`, `infrastructure`, `media`, `other`
- **relevance**: Integer 1-10 (10 = direct Blockstream competitor)
- **reason**: One-sentence explanation

**Filtering rules applied after LLM extraction:**
- Only entities in competitive categories are kept (`competitor`, `exchange`, `wallet`, `protocol`, `infrastructure`)
- Only entities with relevance >= 6 are stored
- Names matching tracked entities are excluded (case-insensitive hallucination guard)
- Names matching dismissed candidates are excluded

### 4. Storage and Alert Firing

Each qualifying entity is stored as a `CandidateEntity` row:
- `name` — the detected name (unique constraint prevents duplicates)
- `mention_count` — incremented each time the name appears across detection cycles
- `sample_urls` — up to 5 article URLs where the entity was found
- `status` — `PENDING` (new), `PROMOTED` (added as competitor), or `DISMISSED` (ignored)
- `category` — LLM-assigned category
- `relevance_score` — LLM-assigned relevance (highest seen is kept)
- `llm_reason` — LLM explanation (updated when a higher relevance is seen)
- `alert_fired` — whether the system has already created a `COMPETITOR_NEW` alert

When a candidate's `mention_count` reaches the threshold (default: 3 mentions across separate fetch cycles), a `COMPETITOR_NEW` alert fires. The alert includes the category and relevance score.

## Managing Candidates in the Dashboard

### Candidates Page (`/candidates/`)

Shows all detected candidates sorted by mention count. Each candidate shows:
- Name and status badge (Pending / Promoted / Dismissed)
- Category badge and relevance score (from LLM)
- LLM's reasoning for why this entity is relevant
- Mention count and first/last seen dates
- Sample article links for quick verification

### Promote

Clicking **Promote** creates a new tracked `Entity` with:
- `news_keywords` auto-populated from the candidate name
- `reddit_keywords` auto-populated from the candidate name
- `github_org` auto-populated via a GitHub API search for an org matching the name (best-effort)
- `is_primary = False` (competitor)

The entity immediately enters the fetcher pipeline — no code changes or admin configuration needed.

### Dismiss

Clicking **Dismiss** marks the candidate as `DISMISSED`. Dismissed names are excluded from future LLM extraction, so they never reappear.

### Dismiss All Pending

Bulk action to clear all pending candidates at once.

## Managing Candidates in Django Admin

The `CandidateEntity` model is registered in Django Admin (`/admin/metrics/candidateentity/`). From there you can:

- **Edit a candidate's name** if the LLM extracted it incorrectly
- **Change status** manually (e.g., un-dismiss a candidate you dismissed by mistake)
- **Reset `alert_fired`** to re-trigger the alert for a candidate
- **Delete** candidates entirely
- **Bulk dismiss** via admin actions
- **View promoted entity** link to check the auto-created `Entity` row

## Configuration

| Setting | Location | Default | Description |
|---------|----------|---------|-------------|
| Mention threshold | `metrics/delta.py` (`COMPETITOR_MENTION_THRESHOLD`) | 3 | Mentions needed before alert fires |
| Min relevance score | `metrics/delta.py` (`MIN_RELEVANCE_SCORE`) | 6 | Minimum LLM relevance (1-10) to store a candidate |
| Competitive categories | `metrics/delta.py` (`COMPETITIVE_CATEGORIES`) | competitor, exchange, wallet, protocol, infrastructure | Categories kept after LLM extraction |
| Max sample URLs | `metrics/delta.py` (`MAX_SAMPLE_URLS`) | 5 | Article URLs stored per candidate |
| Max content length | `metrics/scraper.py` (`MAX_CONTENT_LENGTH`) | 5,000 chars | Article text stored per NewsItem |
| LLM content words | `metrics/scraper.py` (`LLM_CONTENT_WORDS`) | 500 words | Article excerpt length sent to LLM |
| Ollama context budget | `config/settings/base.py` (`OLLAMA_CONTEXT_BUDGET`) | 80,000 tokens | Token budget for Ollama batches |
| Groq context budget | `config/settings/base.py` (`GROQ_CONTEXT_BUDGET`) | 25,000 tokens | Token budget for Groq batches |
| LLM backend | `config/settings/base.py` | Ollama first, Groq fallback | Same dual backend as anomaly detection and briefings |
| News fetch interval | Django Admin > Fetcher Configs > News | 2 hours | How often new articles (and candidates) are processed |

## Architecture Reference

| File | Role |
|------|------|
| `metrics/delta.py` | `run_competitor_detection()`, `_llm_extract_competitors()`, `_build_article_batches()`, constants |
| `metrics/scraper.py` | `scrape_article()`, `truncate_to_words()` — article body extraction with caching |
| `metrics/llm_review.py` | `LLMClient` (Ollama/Groq dual backend, shared with anomaly detection) |
| `metrics/models.py` | `CandidateEntity` model, `NewsItem` model (content fields) |
| `metrics/admin.py` | Django Admin registration for `CandidateEntity` |
| `dashboard/views.py` | `candidate_list`, `promote_candidate`, `dismiss_candidate`, `dismiss_all_candidates` views |
| `dashboard/urls.py` | URL routes for `/candidates/`, promote, dismiss |
| `dashboard/templates/dashboard/candidates.html` | Full candidates page |
| `dashboard/templates/partials/candidate_row.html` | HTMX partial for single candidate card |
| `scheduler/jobs.py` | Integration point: called at end of `run_news_fetcher()` |

## Key Design Decisions

1. **LLM extracts, not filters.** The LLM analyzes articles directly and extracts entities — no regex pre-processing. This eliminates the sentence fragments and generic terms that regex produced.

2. **Article content for context.** Scraping article bodies gives the LLM much richer context than titles alone. A title like "New Player Enters Bitcoin Wallet Space" becomes meaningful when the article body names the company.

3. **Structured output with reasoning.** The LLM returns category, relevance score, and a reason for each extraction. This makes the candidates page self-documenting — you can see *why* each entity was surfaced.

4. **Context-window-aware batching.** Article batches are sized dynamically based on the active LLM backend's context window, ensuring maximum articles per call without exceeding limits.

5. **No regex fallback.** If the LLM is unavailable, detection is skipped entirely rather than falling back to noisy regex extraction. Better to miss a cycle than pollute the candidates list.

6. **Scrape caching.** Article bodies are cached on `NewsItem.content` with a `content_scraped_at` timestamp. Failed scrapes are marked so they aren't retried, and successful scrapes are reused across detection cycles.

7. **Promote is zero-config.** Auto-populates `news_keywords`, `reddit_keywords`, and `github_org` so the new entity enters the fetcher pipeline immediately.

8. **Dismissed candidates stay dismissed.** Once you dismiss a name, it's excluded from future LLM extraction.

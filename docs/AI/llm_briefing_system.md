# AI-Powered Intelligence Briefing System

## AI Technique

**Large Language Model (LLM) — Generative AI for natural-language synthesis.** The system feeds structured competitive data (alerts, metric deltas, news headlines) into a general-purpose LLM and receives a coherent, prioritised markdown briefing. Two backends are supported: Ollama serving local open-weight models (gemma4:e4b) for Docker deployments, and the Groq cloud API for lightweight VPS hosting. The LLM performs extractive and abstractive summarisation over tabular and semi-structured inputs — no fine-tuning, no embeddings, no RAG. Prompt engineering alone drives output quality.

## The Problem

A competitive intelligence dashboard generates a constant stream of raw signals: GitHub metrics changing, news articles appearing, app store ratings shifting, alerts firing. For a team tracking 40+ entities across seven verticals, the volume of data quickly becomes unmanageable. Nobody has time to cross-reference a week of alerts, metric deltas, and headlines to extract what actually matters.

The briefing system solves this by feeding structured competitive data to a large language model and producing a concise, actionable weekly briefing. Instead of scanning dozens of alerts and news items, the product and strategy teams read a single markdown document that highlights threats, opportunities, and entities worth watching.

## How It Works

The system has three stages that run sequentially each time a briefing is generated.

### 1. Data Collection

The generator queries the database for the most recent window of competitive signals (configurable, default 7 days):

- **Alerts** — The 50 most recent alerts, ordered by severity (HIGH first). Each alert carries metadata about what changed: old value, new value, percent delta.
- **Metric deltas** — For every tracked (entity, metric_key) pair, the system computes the percent change between the latest value and the most recent value before the window. The top 10 by absolute change are included.
- **News headlines** — The 20 most recent news items, with their entity tags and publication source.

A distinct entity count is computed across all these sources to give the LLM a sense of how broad the competitive landscape is this week. This logic lives in `briefings/generator.py`, in the `_collect_data` and `_compute_metric_deltas` methods of `BriefingGenerator`.

### 2. Prompt Construction

The collected data is formatted into a structured context block with clearly labelled sections: HIGH/MEDIUM/LOW severity alerts, a metric changes table, and news headlines with entity tags. This context block is appended to output instructions that define the exact markdown structure the LLM must produce:

- **Executive Summary** — 2-3 sentences on the overall competitive landscape.
- **Key Threats** — Competitor movements requiring immediate attention.
- **Opportunities** — Competitor weaknesses, slowdowns, or market gaps.
- **Watch List** — Entities showing unusual activity worth monitoring next week.

A system prompt establishes the LLM's role as a competitive intelligence analyst for Blockstream and enforces brevity (under 400 words). The prompt constants are defined at the top of `briefings/generator.py`.

### 3. LLM Generation and Persistence

The constructed prompt is sent to the resolved LLM backend (see Dual LLM Backend below). The response is stored as a `WeeklyBriefing` record with its status lifecycle: PENDING, GENERATING, READY, or FAILED.

On the dashboard, HTMX polling watches the status of a briefing in progress. When the status transitions to READY, the page reloads to display the rendered markdown. If generation fails, the error message is stored and shown to the user.

After a successful generation, the system optionally delivers the briefing by email to configured recipients. Email delivery is non-fatal — a mail failure never marks the briefing itself as failed.

The briefing model, configuration singletons, and email config live in `briefings/models.py`. The email delivery logic is in `briefings/email.py`.

## Dual LLM Backend

The system supports two LLM backends to accommodate different deployment scenarios.

### Ollama (Local)

Ollama runs as a local service and serves open-weight models. This is the default for local development and Docker-based demos. The Docker Compose stack includes an `ollama` service and an `ollama-init` one-shot service that automatically pulls the configured model (gemma4:e4b) on first start. No manual model download is needed.

The Ollama client is a thin HTTP wrapper in `briefings/generator.py` (class `OllamaClient`) that calls the `/api/chat` endpoint with system and user messages. It uses the `requests` library directly — no LangChain, no vendor SDK.

### Groq (Cloud)

Groq provides a free-tier cloud API with fast inference. This is intended for VPS deployments where running a local LLM would require expensive dedicated VRAM. The Groq client (class `GroqClient` in `briefings/generator.py`) calls the Groq OpenAI-compatible REST endpoint with a Bearer token. Again, plain `requests` — no additional library.

The Groq API key is stored in the database via `BriefingConfig`, not in environment variables. This allows operators to configure it through the settings UI at `/briefing/settings/` without a server restart or access to the filesystem.

### Provider Selection

The `BriefingConfig` singleton has a `llm_provider` field with three options:

- **Auto** — Probes the Ollama service with a lightweight health check. If Ollama responds, it is used. If Ollama is unreachable and a Groq API key is configured, the system falls back to Groq silently with a log message. If neither is available, Ollama is returned anyway and generation will fail with a clear error rather than silently skipping.
- **Ollama** — Always uses Ollama, no fallback.
- **Groq** — Always uses Groq, no Ollama probe.

The factory function `get_llm_client` in `briefings/generator.py` resolves the provider. `BriefingGenerator` calls this factory at init time, so the choice is made once per generation run.

The `model_used` field on each `WeeklyBriefing` record stores the provider and model as a prefixed label (e.g., `ollama:gemma4:e4b` or `groq:llama-3.3-70b-versatile`) so historical briefings are traceable to their backend.

## Getting Started

### Step 1: Choose a provider

Navigate to `/briefing/settings/` in the dashboard. Select your LLM provider:

- For local development or Docker: leave it on **Auto** or select **Ollama**. If running via Docker Compose, Ollama is already provisioned.
- For VPS deployment: select **Groq** (or **Auto** with a Groq key as fallback). Paste your API key from console.groq.com/keys into the API Key field. The eye icon lets you verify the key before saving.

### Step 2: Configure the data window

On the same settings page, set the **Period (days)** to control how many days of alerts, news, and metrics are fed into each briefing. The default of 7 produces a weekly briefing.

### Step 3: Generate a briefing

Go to `/briefing/` and click **Generate Now**. The system creates a PENDING briefing, resolves the LLM backend, collects data, builds the prompt, and calls the model. HTMX polling updates the status in real time. When READY, the page reloads with the full briefing.

### Step 4: Scheduled generation (optional)

The scheduler automatically generates a briefing every Sunday at 10:00 UTC. This is registered in `scheduler/jobs.py` as the `weekly_briefing_job`. Email delivery can be enabled in briefing settings to distribute the briefing automatically.

## Architecture Reference

| File | Role |
|------|------|
| `briefings/generator.py` | Core module: `OllamaClient`, `GroqClient`, `get_llm_client` factory, `BriefingGenerator` pipeline |
| `briefings/models.py` | `WeeklyBriefing` (output), `BriefingConfig` (singleton with provider/model/key settings), `EmailConfig` |
| `briefings/email.py` | SMTP delivery of generated briefings to configured recipients |
| `briefings/admin.py` | Django admin registration for viewing briefings and editing config |
| `dashboard/views.py` | `briefing`, `briefing_generate`, `briefing_settings`, `briefing_models_json` views |
| `dashboard/templates/dashboard/briefing.html` | Briefing display page with HTMX polling |
| `dashboard/templates/dashboard/briefing_settings.html` | Settings form: provider selector, Groq key, Ollama model, email config |
| `dashboard/templates/partials/briefing_status.html` | HTMX partial for real-time generation status |
| `scheduler/jobs.py` | `weekly_briefing_job` — scheduled and on-demand briefing generation |
| `config/settings/base.py` | `OLLAMA_BASE_URL`, `OLLAMA_MODEL` environment defaults |

## Key Design Decisions

1. **No LangChain, no vendor SDKs.** Both Ollama and Groq expose simple REST APIs. The project uses `requests` directly, keeping the dependency footprint minimal and the code transparent. Either client is under 40 lines.

2. **Singleton configuration in the database.** `BriefingConfig` is a singleton (pk=1, enforced in `save()`). This means LLM provider, model, API key, and email settings can all be changed through the web UI without touching environment variables or restarting the server.

3. **All LLM failures are non-fatal.** If neither Ollama nor Groq is available, generation fails gracefully with a stored error message. Email delivery failures never affect briefing status. The dashboard always shows the last successful briefing regardless.

4. **Provider resolution happens once per generation.** The `get_llm_client` factory probes Ollama (in AUTO mode) at the start of generation, not on every request. This avoids repeated health checks during the prompt-build and data-collection phases.

5. **Groq API key lives in the database, not in .env.** For VPS deployments, operators configure the key through the UI. This avoids the need for filesystem access or container rebuilds to change credentials. The key is treated like a password field: masked on display, only overwritten when a new value is submitted.

6. **Briefing output is structured, not freeform.** The system prompt enforces a fixed four-section markdown template (Executive Summary, Key Threats, Opportunities, Watch List). This ensures consistency across different models and makes briefings scannable.

7. **HTMX polling for real-time feedback.** On-demand generation creates a PENDING record with a known primary key, then transitions it through GENERATING to READY/FAILED. The dashboard polls a lightweight partial endpoint and reloads on completion. No WebSockets, no JavaScript state management.

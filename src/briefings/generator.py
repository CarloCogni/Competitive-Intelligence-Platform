# briefings/generator.py
"""
AI-powered weekly intelligence briefing generator.

OllamaClient  — thin HTTP wrapper over the Ollama REST API (local service).
GroqClient    — thin HTTP wrapper over the Groq OpenAI-compatible REST API.
get_llm_client — factory that resolves the right client from BriefingConfig:
                  OLLAMA → OllamaClient
                  GROQ   → GroqClient
                  AUTO   → OllamaClient if reachable, else GroqClient fallback.

BriefingGenerator — pulls the last N days of Alert, Metric, and NewsItem
                    records, builds a structured prompt, calls the resolved
                    LLM client, and persists the result as a WeeklyBriefing.
"""

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import requests
from django.conf import settings
from django.utils import timezone

if TYPE_CHECKING:
    from briefings.models import BriefingConfig, WeeklyBriefing

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt constants
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_TEMPLATE = """\
You are a competitive intelligence analyst for {org_name}, {org_description}.
Your job is to synthesize raw competitive signals into a concise, actionable weekly briefing
for the product and strategy teams.

Rules:
- Be direct and specific. No filler phrases.
- Prioritise high-severity signals.
- Use markdown formatting exactly as requested.
- Keep the total briefing under 400 words.
"""


def _system_prompt() -> str:
    """Return the briefing system prompt for the configured primary organisation."""
    return _SYSTEM_PROMPT_TEMPLATE.format(
        org_name=settings.PRIMARY_ORG_NAME,
        org_description=settings.PRIMARY_ORG_DESCRIPTION,
    )


_OUTPUT_INSTRUCTIONS = """\
---
Generate the weekly briefing using ONLY this markdown structure:

**Executive Summary**
2–3 sentences covering the overall competitive landscape this week.

**Key Threats**
- Bullet list of competitor movements that require immediate attention.

**Opportunities**
- Bullet list of competitor weaknesses, slowdowns, or market gaps to exploit.

**Watch List**
- Bullet list of entities showing unusual activity worth monitoring next week.

Markdown only. No preamble. No conclusion after the Watch List section.\
"""

# ---------------------------------------------------------------------------
# OllamaClient
# ---------------------------------------------------------------------------

# Known free Groq models for the settings UI dropdown
GROQ_FREE_MODELS: list[str] = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "gemma2-9b-it",
    "mixtral-8x7b-32768",
]


class OllamaClient:
    """
    Thin HTTP wrapper around the Ollama local LLM service.

    Uses the /api/chat endpoint (system + user message format) rather than
    /api/generate so the model handles system instructions cleanly.
    """

    def __init__(self, base_url: str | None = None, model: str | None = None) -> None:
        """
        Initialise with Ollama base URL and model identifier.

        Defaults fall back to settings.OLLAMA_BASE_URL / settings.OLLAMA_MODEL.
        """
        self.base_url = (base_url or settings.OLLAMA_BASE_URL).rstrip("/")
        self.model = model or settings.OLLAMA_MODEL

    def is_available(self) -> bool:
        """
        Probe the Ollama service with a lightweight tags request.

        Returns False (never raises) — callers decide how to handle unavailability.
        """
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return resp.status_code == 200
        except requests.RequestException as exc:
            logger.warning("Ollama availability check failed: %s", exc)
            return False

    def chat(self, system: str, user: str, timeout: int = 180) -> str:
        """
        Send a chat request to Ollama and return the model's response text.

        Args:
            system: System-role instructions for the model.
            user:   User-role message (the actual data/prompt context).
            timeout: Request timeout in seconds (LLM inference can be slow).

        Returns:
            The model's response content as a string.

        Raises:
            requests.HTTPError: Non-2xx response from Ollama.
            requests.RequestException: Network-level failure (timeout, etc.).
        """
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
        }
        logger.info("Calling Ollama model '%s' at %s", self.model, self.base_url)
        resp = requests.post(
            f"{self.base_url}/api/chat",
            json=payload,
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"]


# ---------------------------------------------------------------------------
# GroqClient
# ---------------------------------------------------------------------------


class GroqClient:
    """
    Thin HTTP wrapper around the Groq cloud LLM API.

    Groq exposes an OpenAI-compatible REST endpoint, so this mirrors
    OllamaClient's interface (is_available / chat) using requests directly —
    no additional library required.
    """

    _API_URL = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, api_key: str, model: str) -> None:
        """
        Initialise with a Groq API key and model identifier.

        Args:
            api_key: Groq API key from console.groq.com/keys, stored in DB.
            model:   Model ID, e.g. 'llama-3.3-70b-versatile'.
        """
        self.api_key = api_key
        self.model = model

    def is_available(self) -> bool:
        """
        Return True when an API key is configured.

        Groq is a cloud service — connectivity is assumed; we only guard
        against a missing key, which would fail immediately at chat() time.
        """
        return bool(self.api_key)

    def chat(self, system: str, user: str, timeout: int = 60) -> str:
        """
        Send a chat request to Groq and return the model's response text.

        Args:
            system:  System-role instructions for the model.
            user:    User-role message (the actual data/prompt context).
            timeout: Request timeout in seconds.

        Returns:
            The model's response content as a string.

        Raises:
            requests.HTTPError:      Non-2xx response from Groq (e.g. bad key).
            requests.RequestException: Network-level failure.
        """
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        logger.info("Calling Groq model '%s'", self.model)
        resp = requests.post(self._API_URL, json=payload, headers=headers, timeout=timeout)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# LLM client factory
# ---------------------------------------------------------------------------


def get_llm_client(config: "BriefingConfig") -> OllamaClient | GroqClient:
    """
    Resolve and return the appropriate LLM client for the given config.

    Provider resolution:
      GROQ   → GroqClient (always)
      OLLAMA → OllamaClient (always)
      AUTO   → OllamaClient if Ollama is reachable;
               GroqClient if Ollama is down and groq_api_key is set;
               OllamaClient otherwise (will fail at generation time with a
               clear error rather than silently skip generation).
    """
    from briefings.models import BriefingConfig

    provider = config.llm_provider

    if provider == BriefingConfig.LLMProvider.GROQ:
        return GroqClient(api_key=config.groq_api_key, model=config.groq_model)

    if provider == BriefingConfig.LLMProvider.OLLAMA:
        return OllamaClient(model=config.ollama_model)

    # AUTO mode: probe Ollama, fall back to Groq
    ollama = OllamaClient(model=config.ollama_model)
    if ollama.is_available():
        return ollama
    if config.groq_api_key:
        logger.info(
            "Ollama unavailable — switching to Groq fallback (model: %s).", config.groq_model
        )
        return GroqClient(api_key=config.groq_api_key, model=config.groq_model)
    # No fallback configured; return Ollama client which will surface a clear error
    logger.warning(
        "Ollama unreachable and no Groq API key configured. "
        "Briefing generation will fail. Set a Groq key in /briefing/settings/ "
        "or start the Ollama service."
    )
    return ollama


# ---------------------------------------------------------------------------
# BriefingGenerator
# ---------------------------------------------------------------------------


class BriefingGenerator:
    """
    Orchestrates the full briefing pipeline.

    1. Collect Alert, Metric, and NewsItem records for the period window.
    2. Build a structured prompt context block.
    3. Call OllamaClient to generate the markdown briefing.
    4. Persist the result as a WeeklyBriefing (status=ready or failed).
    """

    def __init__(self, client: OllamaClient | GroqClient | None = None) -> None:
        """
        Allow injecting a pre-configured client (useful for tests).

        When no client is provided, resolves the correct backend (Ollama or
        Groq) via get_llm_client() based on BriefingConfig.llm_provider.
        """
        if client is None:
            from briefings.models import BriefingConfig

            config = BriefingConfig.get()
            client = get_llm_client(config)
        self.client = client

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _model_label(self) -> str:
        """Return a human-readable label like 'groq:llama-3.3-70b-versatile'."""
        prefix = "groq" if isinstance(self.client, GroqClient) else "ollama"
        return f"{prefix}:{self.client.model}"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(  # type: ignore[name-defined]
        self,
        period_days: int = 7,
        existing_briefing: "WeeklyBriefing | None" = None,
    ) -> "WeeklyBriefing":
        """
        Generate a briefing covering the last `period_days` days.

        When `existing_briefing` is provided (on-demand path), that record is
        transitioned PENDING → GENERATING → READY/FAILED in-place so the UI
        polling the pre-created pk sees the final status.

        When `existing_briefing` is None (scheduled path), a new record is
        created as before.

        Returns:
            The persisted WeeklyBriefing instance.
        """
        from briefings.models import WeeklyBriefing

        now = timezone.now()
        cutoff = now - timedelta(days=period_days)

        if existing_briefing is not None:
            # On-demand path: update the pre-created PENDING record in-place
            # so the HTMX polling endpoint (which knows this pk) sees the transition.
            briefing = existing_briefing
            briefing.status = WeeklyBriefing.Status.GENERATING
            briefing.model_used = self._model_label()
            briefing.period_start = cutoff
            briefing.period_end = now
            briefing.save(update_fields=["status", "model_used", "period_start", "period_end"])
        else:
            # Scheduled path: create a fresh record as before.
            briefing = WeeklyBriefing.objects.create(
                period_start=cutoff,
                period_end=now,
                model_used=self._model_label(),
                status=WeeklyBriefing.Status.GENERATING,
            )
        logger.info(
            "Starting briefing generation (pk=%d, period=%d days).", briefing.pk, period_days
        )

        try:
            data = self._collect_data(cutoff)
            briefing.alert_count = len(data["alerts"])
            briefing.entity_count = data["entity_count"]
            briefing.news_count = len(data["news"])
            briefing.save(update_fields=["alert_count", "entity_count", "news_count"])

            user_context = self._build_context_block(data, cutoff, now)
            content = self.client.chat(
                system=_system_prompt(),
                user=user_context + "\n\n" + _OUTPUT_INSTRUCTIONS,
            )

            briefing.content = content
            briefing.status = WeeklyBriefing.Status.READY
            briefing.generated_at = timezone.now()
            briefing.save(update_fields=["content", "status", "generated_at"])
            logger.info(
                "Briefing pk=%d ready: %d alerts, %d entities, %d news items.",
                briefing.pk,
                briefing.alert_count,
                briefing.entity_count,
                briefing.news_count,
            )

            # Deliver by email — non-fatal; a mail failure never marks briefing as failed.
            try:
                from briefings.email import send_briefing_email

                send_briefing_email(briefing)
            except Exception as exc:
                logger.exception(
                    "Briefing email delivery raised unexpectedly (pk=%d): %s", briefing.pk, exc
                )

        except Exception as exc:
            briefing.status = WeeklyBriefing.Status.FAILED
            briefing.error_message = str(exc)
            briefing.save(update_fields=["status", "error_message"])
            logger.exception("Briefing generation failed (pk=%d): %s", briefing.pk, exc)

        return briefing

    # ------------------------------------------------------------------
    # Data collection
    # ------------------------------------------------------------------

    def _collect_data(self, cutoff: Any) -> dict:
        """
        Query the DB for alerts, metric deltas, and news items since cutoff.

        Returns a dict with keys: alerts, metric_deltas, news, entity_count.
        """
        from metrics.models import Alert, NewsItem

        # Alerts from the period, ordered by severity (HIGH first) then recency
        alerts = list(
            Alert.objects.filter(created_at__gte=cutoff)
            .select_related("entity")
            .order_by("-created_at")[:50]
        )

        # Metric deltas: compare each entity's latest value vs. one period earlier
        metric_deltas = self._compute_metric_deltas(cutoff)

        # Recent news headlines
        news = list(
            NewsItem.objects.filter(published_at__gte=cutoff)
            .prefetch_related("entities")
            .order_by("-published_at")[:20]
        )

        # Distinct entity count across alerts + news
        entity_ids: set[int] = {a.entity_id for a in alerts}
        for item in news:
            entity_ids.update(item.entities.values_list("pk", flat=True))

        return {
            "alerts": alerts,
            "metric_deltas": metric_deltas,
            "news": news,
            "entity_count": len(entity_ids),
        }

    def _compute_metric_deltas(self, cutoff: Any) -> list[dict]:
        """
        Compute top metric changes since cutoff for all entities.

        Strategy: for each (entity, key) pair, take the latest Metric value
        and the latest value older than cutoff as the baseline. Return the
        top 10 by absolute percentage change.
        """
        from django.db.models import OuterRef, Subquery

        from metrics.models import Metric

        # Latest metric value per (entity, key) — captured after cutoff
        latest_subq = (
            Metric.objects.filter(entity=OuterRef("entity"), key=OuterRef("key"))
            .order_by("-captured_at")
            .values("value")[:1]
        )
        # Baseline metric value per (entity, key) — captured before cutoff
        baseline_subq = (
            Metric.objects.filter(
                entity=OuterRef("entity"),
                key=OuterRef("key"),
                captured_at__lt=cutoff,
            )
            .order_by("-captured_at")
            .values("value")[:1]
        )

        # Annotate the latest metrics with their baseline value
        recent = (
            Metric.objects.filter(captured_at__gte=cutoff)
            .select_related("entity")
            .annotate(
                latest_val=Subquery(latest_subq),
                baseline_val=Subquery(baseline_subq),
            )
            .values("entity__name", "key", "latest_val", "baseline_val")
            .distinct()
        )

        deltas = []
        seen: set[tuple] = set()
        for row in recent:
            key = (row["entity__name"], row["key"])
            if key in seen:
                continue
            seen.add(key)

            latest = row["latest_val"]
            baseline = row["baseline_val"]
            # Skip if either value is missing or baseline is zero (avoid divide-by-zero)
            if latest is None or baseline is None or baseline == 0:
                continue

            pct_change = (latest - baseline) / abs(baseline) * 100
            deltas.append(
                {
                    "entity": row["entity__name"],
                    "metric": row["key"],
                    "latest": latest,
                    "baseline": baseline,
                    "pct_change": pct_change,
                }
            )

        # Sort by absolute change descending, return top 10
        deltas.sort(key=lambda x: abs(x["pct_change"]), reverse=True)
        return deltas[:10]

    # ------------------------------------------------------------------
    # Prompt building
    # ------------------------------------------------------------------

    def _build_context_block(self, data: dict, cutoff: Any, now: Any) -> str:
        """
        Build the structured context string that feeds the LLM prompt.

        Sections: date range, high/medium/low alerts, metric changes table,
        news headlines. Each section is prefixed with a markdown heading.
        """
        lines: list[str] = []

        # Header
        lines.append(
            f"Competitive intelligence data for "
            f"{cutoff:%Y-%m-%d} – {now:%Y-%m-%d} "
            f"({data['entity_count']} entities tracked):\n"
        )

        # --- Alerts by severity ---
        alerts_by_severity = self._group_alerts_by_severity(data["alerts"])
        for tier in ("HIGH", "MEDIUM", "LOW"):
            tier_alerts = alerts_by_severity.get(tier, [])
            lines.append(f"## {tier} SEVERITY ALERTS")
            if not tier_alerts:
                lines.append("(none)\n")
                continue
            for alert in tier_alerts:
                meta = alert.metadata or {}
                old_val = meta.get("old_value", "")
                new_val = meta.get("new_value", "")
                delta = meta.get("delta_pct", "")
                if delta:
                    # Format as percentage
                    try:
                        delta_str = f" ({float(delta):+.1f}%)"
                    except (ValueError, TypeError):
                        delta_str = f" ({delta})"
                else:
                    delta_str = ""
                val_range = f" [{old_val} → {new_val}]" if old_val and new_val else ""
                lines.append(f"- {alert.entity.name}: {alert.message}{delta_str}{val_range}")
            lines.append("")

        # --- Metric changes table ---
        lines.append("## TOP METRIC CHANGES (7-day delta)")
        if data["metric_deltas"]:
            lines.append("| Entity | Metric | Change | Latest |")
            lines.append("|--------|--------|--------|--------|")
            for d in data["metric_deltas"]:
                sign = "+" if d["pct_change"] >= 0 else ""
                lines.append(
                    f"| {d['entity']} | {d['metric']} "
                    f"| {sign}{d['pct_change']:.1f}% "
                    f"| {d['latest']:,.0f} |"
                )
        else:
            lines.append("(no metric history available yet)")
        lines.append("")

        # --- News headlines ---
        lines.append("## NEWS HEADLINES")
        if data["news"]:
            for item in data["news"]:
                entity_names = ", ".join(e.name for e in item.entities.all()[:3])
                pub = item.published_at.strftime("%b %d") if item.published_at else "?"
                source = f" ({item.source_name})" if item.source_name else ""
                tag = f" [{entity_names}]" if entity_names else ""
                lines.append(f'- "{item.title}"{source}{tag} — {pub}')
        else:
            lines.append("(no news items this week)")
        lines.append("")

        return "\n".join(lines)

    def _group_alerts_by_severity(self, alerts: list) -> dict[str, list]:
        """
        Group alert list by severity tier (HIGH / MEDIUM / LOW).

        Severity is stored in alert.metadata["severity"]. Alerts without
        a severity key are treated as MEDIUM.
        """
        groups: dict[str, list] = {"HIGH": [], "MEDIUM": [], "LOW": []}
        for alert in alerts:
            sev = (alert.metadata or {}).get("severity", "MEDIUM").upper()
            if sev not in groups:
                sev = "MEDIUM"
            groups[sev].append(alert)
        return groups

# metrics/news_enrichment.py
"""
LLM-powered sentiment and signal classification for news articles.

Processes scraped NewsItem articles in batches, classifying each with:
- sentiment (positive / neutral / negative / mixed)
- signal_type (product_launch, partnership, funding, vulnerability, etc.)
- llm_summary (1-2 sentence strategic summary)
- key_quote (most strategic sentence from the article, verbatim)

Ollama-only by default (NEWS_ENRICHMENT_OLLAMA_ONLY setting) to avoid
silently burning Groq API tokens on high-volume article processing.
Non-fatal: LLM failures are logged and skipped silently.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Approximate characters per token for budget estimation
_CHARS_PER_TOKEN = 4

# Token overhead reserved for system prompt
_SYSTEM_PROMPT_OVERHEAD = 2_000

# Maximum articles per LLM batch
_MAX_ARTICLES_PER_BATCH = 50

# How far back to look for un-enriched articles (hours)
_LOOKBACK_HOURS = 72

# Valid sentiment values from LLM output
_VALID_SENTIMENTS = {"positive", "neutral", "negative", "mixed"}

# Valid signal types from LLM output
_VALID_SIGNAL_TYPES = {
    "product_launch",
    "partnership",
    "funding",
    "vulnerability",
    "regulation",
    "hiring",
    "opinion",
    "other",
}

# System prompt for sentiment and signal classification
_ENRICHMENT_SYSTEM_PROMPT = """\
You are a competitive intelligence analyst classifying news articles about \
Bitcoin, cryptocurrency, and fintech companies.

For each article, provide:
- sentiment: one of "positive", "neutral", "negative", "mixed"
  (positive = good news for the mentioned entity, negative = bad news, \
mixed = contains both, neutral = factual/informational)
- signal_type: one of "product_launch", "partnership", "funding", \
"vulnerability", "regulation", "hiring", "opinion", "other"
- summary: 1-2 sentence strategic summary focusing on competitive implications
- key_quote: the single most strategically relevant sentence from the article \
(copy verbatim from the content; if no content is available, extract from the title)

Rules:
- Classify every article in the batch — do not skip any.
- If content is empty, classify based on the title alone.
- Respond ONLY with valid JSON matching the schema below. No markdown fences, \
no explanation, no extra text.

Response schema:
[
  {{"article_index": 0, "sentiment": "neutral", "signal_type": "other", \
"summary": "...", "key_quote": "..."}}
]\
"""

# System prompt for Key Takeaways generation
_TAKEAWAYS_SYSTEM_PROMPT = """\
You are a competitive intelligence analyst for a Bitcoin infrastructure company.
Given a list of recent news article summaries, produce 3-5 concise bullet points \
highlighting the most strategically significant developments.

Focus on:
- Competitive threats and opportunities
- Market shifts and emerging trends
- Product launches or vulnerabilities that affect the competitive landscape

Each bullet should be 1-2 sentences. Be direct and actionable.
Do NOT add a preamble or closing remarks — just the bullet points.\
"""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _estimate_tokens(text: str) -> int:
    """Estimate token count from character length."""
    return len(text) // _CHARS_PER_TOKEN


def _get_enrichment_context_budget() -> int:
    """
    Return the token budget based on NEWS_ENRICHMENT_OLLAMA_ONLY setting.

    When Ollama-only, always uses OLLAMA_CONTEXT_BUDGET.
    When fallback is allowed, checks Ollama availability first.
    """
    if getattr(settings, "NEWS_ENRICHMENT_OLLAMA_ONLY", True):
        return getattr(settings, "OLLAMA_CONTEXT_BUDGET", 80_000)

    # Fallback allowed — check which backend is active
    try:
        from metrics.llm_review import LLMClient

        client = LLMClient.from_settings()
        if client.is_ollama_available():
            return getattr(settings, "OLLAMA_CONTEXT_BUDGET", 80_000)
    except Exception:
        pass
    return getattr(settings, "GROQ_CONTEXT_BUDGET", 25_000)


def _build_article_batches(
    articles: list[tuple[int, str, str, str]],
    context_budget: int,
) -> list[list[tuple[int, str, str, str]]]:
    """
    Split articles into batches that fit within the context budget.

    Each article tuple is ``(newsitem_pk, title, content_excerpt, url)``.
    """
    usable = max(context_budget - _SYSTEM_PROMPT_OVERHEAD, 4_000)
    batches: list[list[tuple[int, str, str, str]]] = []
    current_batch: list[tuple[int, str, str, str]] = []
    current_tokens = 0

    for article in articles:
        _, title, content, _ = article
        article_tokens = _estimate_tokens(title) + _estimate_tokens(content) + 20
        batch_full = (current_tokens + article_tokens) > usable
        batch_full = batch_full or len(current_batch) >= _MAX_ARTICLES_PER_BATCH
        if current_batch and batch_full:
            batches.append(current_batch)
            current_batch = []
            current_tokens = 0
        current_batch.append(article)
        current_tokens += article_tokens

    if current_batch:
        batches.append(current_batch)
    return batches


def _strip_markdown_fences(text: str) -> str:
    """Remove markdown code fences from LLM output."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        try:
            first_nl = cleaned.index("\n")
            cleaned = cleaned[first_nl + 1 :]
        except ValueError:
            pass
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].strip()
    return cleaned


def _llm_classify_batch(
    client: object,
    batch: list[tuple[int, str, str, str]],
) -> list[dict]:
    """
    Send a batch of articles to the LLM for sentiment/signal classification.

    Each article tuple is ``(newsitem_pk, title, content_excerpt, url)``.

    Returns a list of dicts with keys:
    ``article_index``, ``sentiment``, ``signal_type``, ``summary``, ``key_quote``.

    Non-fatal: returns empty list on LLM failure.
    """
    # Build numbered article list for the user prompt
    lines = [f"Classify these {len(batch)} articles:\n"]
    for idx, (_, title, content, _) in enumerate(batch):
        lines.append(f"[Article {idx}]")
        lines.append(f"Title: {title}")
        if content:
            lines.append(f"Content: {content}")
        lines.append("")
    user_prompt = "\n".join(lines)

    try:
        raw = client.chat(system=_ENRICHMENT_SYSTEM_PROMPT, user=user_prompt, timeout=90)
    except Exception as exc:
        logger.warning("News enrichment LLM call failed: %s", exc)
        return []

    # Parse JSON response
    cleaned = _strip_markdown_fences(raw)
    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.warning("News enrichment: LLM returned invalid JSON: %s", exc)
        return []

    if not isinstance(result, list):
        logger.warning("News enrichment: LLM returned non-list response.")
        return []

    # Validate and normalize each classification
    classifications: list[dict] = []
    for item in result:
        if not isinstance(item, dict):
            continue
        article_idx = item.get("article_index")
        if not isinstance(article_idx, int) or article_idx < 0 or article_idx >= len(batch):
            continue

        sentiment = str(item.get("sentiment", "")).lower().strip()
        signal_type = str(item.get("signal_type", "")).lower().strip()
        summary = str(item.get("summary", "")).strip()
        key_quote = str(item.get("key_quote", "")).strip()

        # Normalize invalid values to safe defaults
        if sentiment not in _VALID_SENTIMENTS:
            sentiment = "neutral"
        if signal_type not in _VALID_SIGNAL_TYPES:
            signal_type = "other"

        classifications.append(
            {
                "article_index": article_idx,
                "sentiment": sentiment,
                "signal_type": signal_type,
                "summary": summary,
                "key_quote": key_quote,
            }
        )

    logger.info(
        "News enrichment: batch of %d articles → %d classified.",
        len(batch),
        len(classifications),
    )
    return classifications


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_news_enrichment() -> int:
    """
    Enrich recent un-enriched news articles with LLM sentiment and classification.

    Pipeline:
    1. Check LLM availability (Ollama-only by default).
    2. Query un-enriched articles with scraped content.
    3. Build context-window-aware batches.
    4. Send each batch to LLM for classification.
    5. Upsert sentiment, signal_type, llm_summary, key_quote on each NewsItem.

    Non-fatal: if the LLM is unavailable, logs info and returns 0.

    Returns the number of articles enriched.
    """
    from metrics.llm_review import LLMClient
    from metrics.models import NewsItem
    from metrics.scraper import scrape_article, truncate_to_words

    ollama_only = getattr(settings, "NEWS_ENRICHMENT_OLLAMA_ONLY", True)

    # Initialise LLM client and check availability
    try:
        client = LLMClient.from_settings()
    except Exception as exc:
        logger.warning("News enrichment: cannot initialise LLM client: %s", exc)
        return 0

    if ollama_only:
        if not client.is_ollama_available():
            logger.info(
                "News enrichment: Ollama unavailable and NEWS_ENRICHMENT_OLLAMA_ONLY=True, "
                "skipping enrichment (no Groq fallback)."
            )
            return 0
    else:
        # With fallback allowed, still check that at least one backend is available
        if not client.is_ollama_available() and not settings.GROQ_API_KEY:
            logger.warning("News enrichment: no LLM backend available, skipping.")
            return 0

    # Query un-enriched articles with scraped content from the lookback window
    cutoff = timezone.now() - timedelta(hours=_LOOKBACK_HOURS)
    unenriched = list(
        NewsItem.objects.filter(
            enriched_at__isnull=True,
            fetched_at__gte=cutoff,
        ).order_by("-published_at")
    )

    if not unenriched:
        logger.info("News enrichment: no un-enriched articles found.")
        return 0

    # Scrape content for articles that haven't been scraped yet
    articles: list[tuple[int, str, str, str]] = []
    for item in unenriched:
        body = scrape_article(item)
        excerpt = truncate_to_words(body) if body else ""
        articles.append((item.pk, item.title, excerpt, item.url))

    logger.info("News enrichment: processing %d articles.", len(articles))

    # Build batches respecting context window limits
    context_budget = _get_enrichment_context_budget()
    batches = _build_article_batches(articles, context_budget)
    logger.info(
        "News enrichment: %d articles split into %d batch(es) (budget=%d tokens).",
        len(articles),
        len(batches),
        context_budget,
    )

    # Classify each batch and collect results keyed by article PK
    pk_to_classification: dict[int, dict] = {}
    for batch in batches:
        classifications = _llm_classify_batch(client, batch)
        for cls in classifications:
            idx = cls["article_index"]
            pk = batch[idx][0]  # newsitem_pk from the tuple
            pk_to_classification[pk] = cls

    if not pk_to_classification:
        logger.info("News enrichment: LLM returned no classifications.")
        return 0

    # Bulk update NewsItem records with enrichment data
    now = timezone.now()
    enriched_count = 0
    for item in unenriched:
        cls = pk_to_classification.get(item.pk)
        if not cls:
            continue
        item.sentiment = cls["sentiment"]
        item.signal_type = cls["signal_type"]
        item.llm_summary = cls["summary"]
        item.key_quote = cls["key_quote"]
        item.enriched_at = now
        item.save(
            update_fields=["sentiment", "signal_type", "llm_summary", "key_quote", "enriched_at"]
        )
        enriched_count += 1

    logger.info("News enrichment: %d articles enriched successfully.", enriched_count)
    return enriched_count


def generate_key_takeaways(days: int = 7) -> str:
    """
    Generate on-demand executive takeaways from recently enriched articles.

    Uses LLM summaries (not raw content) for a compact prompt. Returns HTML
    bullet list string, or an error message if the LLM is unavailable.

    Args:
        days: Number of days to look back for enriched articles.

    Returns:
        HTML string with bullet points, or an error/empty message.
    """
    from metrics.llm_review import LLMClient
    from metrics.models import NewsItem

    # Initialise LLM client — availability is checked inside chat()
    try:
        client = LLMClient.from_settings()
    except Exception:
        return '<p class="text-gray-500 text-sm">LLM client unavailable.</p>'

    # Collect enriched article summaries from the lookback period
    cutoff = timezone.now() - timedelta(days=days)
    articles = (
        NewsItem.objects.filter(
            enriched_at__isnull=False,
            published_at__gte=cutoff,
        )
        .exclude(llm_summary="")
        .select_related()
        .prefetch_related("entities")
        .order_by("-published_at")[:50]
    )

    if not articles:
        return '<p class="text-gray-500 text-sm">No enriched articles in this period.</p>'

    # Build compact prompt from pre-generated summaries
    lines = [f"Here are {len(articles)} recent news summaries from the past {days} days:\n"]
    for i, article in enumerate(articles):
        entity_names = ", ".join(e.name for e in article.entities.all()[:3])
        sentiment_tag = f"[{article.sentiment}]" if article.sentiment else ""
        lines.append(f"{i + 1}. {sentiment_tag} ({entity_names}) {article.llm_summary}")
    user_prompt = "\n".join(lines)

    try:
        raw = client.chat(system=_TAKEAWAYS_SYSTEM_PROMPT, user=user_prompt, timeout=120)
    except Exception as exc:
        logger.warning("Key takeaways generation failed: %s", exc)
        return '<p class="text-red-400 text-sm">Failed to generate takeaways. Try again later.</p>'

    # Convert bullet points to HTML list
    takeaways_html = _bullets_to_html(raw.strip())
    return takeaways_html


def _bullets_to_html(text: str) -> str:
    """Convert plain-text bullet points to an HTML unordered list."""
    lines = text.strip().split("\n")
    items = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # Strip common bullet prefixes
        for prefix in ("- ", "* ", "• "):
            if line.startswith(prefix):
                line = line[len(prefix) :]
                break
        # Strip numbered prefixes like "1. ", "2) "
        if len(line) > 2 and line[0].isdigit() and line[1] in (".", ")"):
            line = line[2:].strip()
        elif len(line) > 3 and line[:2].isdigit() and line[2] in (".", ")"):
            line = line[3:].strip()
        if line:
            items.append(f"<li>{line}</li>")

    if not items:
        return f'<p class="text-gray-400 text-sm">{text}</p>'

    return (
        '<ul class="list-disc list-inside space-y-2 text-sm text-gray-300">'
        + "".join(items)
        + "</ul>"
    )

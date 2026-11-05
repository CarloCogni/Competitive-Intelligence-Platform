# metrics/delta.py
"""
Delta detection logic.

Two detection functions are exported:

run_delta_detection(source)
    Compares the latest Metric value against a time-windowed baseline and fires
    a METRIC_SPIKE Alert when the percentage change exceeds a configured threshold.

    Why time-windowed baseline instead of comparing consecutive rows:
        If the fetcher runs several times with no change, comparing row[N] vs row[N-1]
        always yields 0 % delta. A real spike from several days ago becomes invisible.
        Comparing against the most recent reading from >= lookback_days ago captures
        genuine trends regardless of fetch frequency.

    Thresholds are stored in the DeltaThreshold model and editable via Django Admin.
    Run `python manage.py migrate` then seed initial rows with the data migration.

run_release_detection()
    Compares the two most recent GitHub MetricSnapshot objects per entity and fires:
      - NEW_RELEASE — when latest_release_published_at changes between snapshots.
      - NEW_REPO    — when public_repos count increases between snapshots.
    Deduplicates by checking for an existing identical Alert before creating.

Usage:
    from metrics.delta import run_delta_detection, run_release_detection
    run_delta_detection("github")
    run_release_detection()
"""

import logging

from django.conf import settings
from django.utils import timezone

from metrics.anomaly import AnomalyDetector, ModelNotFoundError
from metrics.models import Alert, CompoundRule, DeltaThreshold, Metric, MetricSnapshot

logger = logging.getLogger(__name__)


def run_release_detection() -> int:
    """
    Detect new GitHub releases and new repos by comparing the two most recent
    MetricSnapshot objects per entity (GitHub source only).

    Fires:
      - NEW_RELEASE — latest_release_published_at changed between the two snapshots.
      - NEW_REPO    — public_repos count increased between the two snapshots.

    Both checks deduplicate: an identical alert is not created if one already exists
    with the same metadata key (release tag or repo count).

    Returns the number of alerts created.
    """
    alerts_created = 0

    # Find all entity IDs that have at least two GitHub snapshots
    entity_ids = list(
        MetricSnapshot.objects.filter(source="github")
        .order_by("entity_id")
        .values_list("entity_id", flat=True)
        .distinct()
    )

    for entity_id in entity_ids:
        # Fetch the two most recent snapshots for this entity
        snapshots = list(
            MetricSnapshot.objects.select_related("entity")
            .filter(entity_id=entity_id, source="github")
            .order_by("-captured_at")[:2]
        )

        if len(snapshots) < 2:
            # Not enough history to compare — skip
            continue

        current_data: dict = snapshots[0].data
        previous_data: dict = snapshots[1].data

        # --- NEW_RELEASE detection ---
        current_tag = current_data.get("latest_release_tag", "")
        previous_tag = previous_data.get("latest_release_tag", "")
        current_published = current_data.get("latest_release_published_at", "")

        if current_tag and current_tag != previous_tag:
            # Deduplicate: don't fire if we already alerted on this exact tag
            already_alerted = Alert.objects.filter(
                entity_id=entity_id,
                alert_type=Alert.AlertType.NEW_RELEASE,
                metadata__release_tag=current_tag,
            ).exists()

            if not already_alerted:
                entity = snapshots[0].entity
                # Construct a direct link to the release page when org+repo are configured
                release_url = (
                    f"https://github.com/{entity.github_org}/{entity.github_repo}/releases/tag/{current_tag}"
                    if entity.github_org and entity.github_repo
                    else ""
                )
                Alert.objects.create(
                    entity_id=entity_id,
                    alert_type=Alert.AlertType.NEW_RELEASE,
                    message=f"New release detected: {current_tag}",
                    metadata={
                        "release_tag": current_tag,
                        "published_at": current_published,
                        "previous_tag": previous_tag,
                        "release_url": release_url,
                    },
                )
                logger.info("NEW_RELEASE alert: entity_id=%d tag=%s", entity_id, current_tag)
                alerts_created += 1

        # --- NEW_REPO detection ---
        current_repos = current_data.get("public_repos")
        previous_repos = previous_data.get("public_repos")

        if (
            current_repos is not None
            and previous_repos is not None
            and current_repos > previous_repos
        ):
            # Deduplicate: don't fire if we already alerted on this exact new count
            already_alerted = Alert.objects.filter(
                entity_id=entity_id,
                alert_type=Alert.AlertType.NEW_REPO,
                metadata__new_count=current_repos,
            ).exists()

            if not already_alerted:
                Alert.objects.create(
                    entity_id=entity_id,
                    alert_type=Alert.AlertType.NEW_REPO,
                    message=(
                        f"Public repo count increased from {previous_repos} to {current_repos}."
                    ),
                    metadata={
                        "previous_count": previous_repos,
                        "new_count": current_repos,
                    },
                )
                logger.info(
                    "NEW_REPO alert: entity_id=%d repos=%d→%d",
                    entity_id,
                    previous_repos,
                    current_repos,
                )
                alerts_created += 1

    logger.info("Release detection: created %d alert(s).", alerts_created)
    return alerts_created


def run_changelog_release_detection() -> int:
    """
    Detect new releases from the changelog (releases.atom) source by
    comparing the latest_tag in consecutive MetricSnapshot payloads.

    Fires NEW_RELEASE alerts with metadata.source="changelog" to
    deduplicate independently from the GitHub API-based run_release_detection().

    Returns the number of alerts created.
    """
    alerts_created = 0

    entity_ids = list(
        MetricSnapshot.objects.filter(source="changelog")
        .order_by("entity_id")
        .values_list("entity_id", flat=True)
        .distinct()
    )

    for entity_id in entity_ids:
        snapshots = list(
            MetricSnapshot.objects.select_related("entity")
            .filter(entity_id=entity_id, source="changelog")
            .order_by("-captured_at")[:2]
        )

        if len(snapshots) < 2:
            continue

        current_tag = snapshots[0].data.get("latest_tag", "")
        previous_tag = snapshots[1].data.get("latest_tag", "")

        if not current_tag or current_tag == previous_tag:
            continue

        # Deduplicate: check metadata for both release_tag AND source=changelog
        already_alerted = Alert.objects.filter(
            entity_id=entity_id,
            alert_type=Alert.AlertType.NEW_RELEASE,
            metadata__release_tag=current_tag,
            metadata__source="changelog",
        ).exists()

        if already_alerted:
            continue

        entity = snapshots[0].entity
        release_url = snapshots[0].data.get("latest_url", "")
        # Fallback: construct URL from entity GitHub identifiers when feed omits link
        if not release_url and entity.github_org and entity.github_repo:
            release_url = (
                f"https://github.com/{entity.github_org}/{entity.github_repo}"
                f"/releases/tag/{current_tag}"
            )
        Alert.objects.create(
            entity_id=entity_id,
            alert_type=Alert.AlertType.NEW_RELEASE,
            message=f"New release detected (changelog): {current_tag}",
            metadata={
                "release_tag": current_tag,
                "previous_tag": previous_tag,
                "published_at": snapshots[0].data.get("latest_published", ""),
                "release_url": release_url,
                "source": "changelog",
            },
        )
        logger.info("NEW_RELEASE (changelog) alert: entity_id=%d tag=%s", entity_id, current_tag)
        alerts_created += 1

    logger.info("Changelog release detection: created %d alert(s).", alerts_created)
    return alerts_created


def run_delta_detection(source: str) -> int:
    """
    Run delta detection for all metrics from a given source.

    For each (entity, metric_key) pair:
      1. Load the DeltaThreshold for this key (skip if none configured).
      2. Get the most recent Metric reading (latest).
      3. Get the most recent Metric reading from >= lookback_days ago (baseline).
      4. If |pct_change| >= threshold_pct, create an Alert.

    Thresholds are loaded once from the DB at the start of each run.
    Returns the number of alerts created.
    """
    # Load all thresholds in one query: {metric_key: DeltaThreshold}
    thresholds: dict[str, DeltaThreshold] = {
        dt.metric_key: dt for dt in DeltaThreshold.objects.all()
    }

    alerts_created = 0
    now = timezone.now()

    # Find all (entity_id, key) pairs that have metrics from this source.
    # .order_by() clears the model's Meta.ordering so that DISTINCT operates
    # only on the two selected columns — avoids "SELECT DISTINCT x,y ORDER BY z"
    # which causes SQLite to return non-distinct rows.
    pairs = list(
        Metric.objects.filter(source=source)
        .order_by("entity_id", "key")
        .values_list("entity_id", "key")
        .distinct()
    )

    for entity_id, key in pairs:
        dt = thresholds.get(key)
        if dt is None:
            # No threshold configured for this key — skip
            continue

        # Each key can have its own lookback window
        cutoff = now - timezone.timedelta(days=dt.lookback_days)

        # Latest reading
        latest = (
            Metric.objects.filter(entity_id=entity_id, key=key).order_by("-captured_at").first()
        )
        if latest is None or latest.value is None:
            continue

        # Baseline: most recent reading before the lookback cutoff
        baseline = (
            Metric.objects.filter(entity_id=entity_id, key=key, captured_at__lte=cutoff)
            .order_by("-captured_at")
            .first()
        )
        if baseline is None or baseline.value is None:
            # Not enough history yet — skip
            continue

        if baseline.value == 0:
            # Avoid division by zero; skip zero baselines
            continue

        pct_change = (latest.value - baseline.value) / baseline.value

        # --- Dynamic threshold path (Isolation Forest / cold-start) ---
        if dt.use_dynamic_threshold:
            detector = AnomalyDetector(
                model_dir=settings.ANOMALY_MODEL_DIR,
                entity_id=entity_id,
                metric_key=key,
            )
            try:
                result = detector.score(pct_change)
            except ModelNotFoundError:
                logger.warning(
                    "No anomaly model for entity_id=%d key=%s; falling back to static.",
                    entity_id,
                    key,
                )
                # Fall through to static check below
            else:
                if result.is_anomaly:
                    _create_alert(
                        entity_id=entity_id,
                        key=key,
                        latest=latest,
                        baseline=baseline,
                        pct_change=pct_change,
                        threshold_pct=result.p95_abs_change,
                        lookback_days=dt.lookback_days,
                    )
                    alerts_created += 1
                continue  # Model found — skip static check regardless

        # --- Static threshold path (unchanged) ---
        if abs(pct_change) >= dt.threshold_pct:
            _create_alert(
                entity_id=entity_id,
                key=key,
                latest=latest,
                baseline=baseline,
                pct_change=pct_change,
                threshold_pct=dt.threshold_pct,
                lookback_days=dt.lookback_days,
            )
            alerts_created += 1

    logger.info(
        "Delta detection (%s): checked %d pair(s), created %d alert(s).",
        source,
        len(pairs),
        alerts_created,
    )
    return alerts_created


def _compute_severity(delta_pct: float, threshold_pct: float) -> str:
    """
    Compute severity tier based on how many times delta_pct exceeds threshold_pct.

    Both arguments are fractional (e.g. 0.50 for 50%, 0.20 for 20%).

    LOW:    1× ≤ ratio < 2× (just barely triggered)
    MEDIUM: 2× ≤ ratio < 5×
    HIGH:   ratio ≥ 5×
    """
    ratio = abs(delta_pct) / threshold_pct
    if ratio < 2:
        return "LOW"
    if ratio < 5:
        return "MEDIUM"
    return "HIGH"


def _create_alert(
    entity_id: int,
    key: str,
    latest: Metric,
    baseline: Metric,
    pct_change: float,
    threshold_pct: float,
    lookback_days: int,
) -> None:
    """Create a METRIC_SPIKE Alert for a threshold-crossing metric."""
    direction = "up" if pct_change > 0 else "down"
    pct_display = f"{abs(pct_change) * 100:.1f}%"

    message = (
        f"{key} moved {direction} {pct_display} "
        f"(from {baseline.value:.1f} to {latest.value:.1f}) "
        f"over {lookback_days} days."
    )

    Alert.objects.create(
        entity_id=entity_id,
        alert_type=Alert.AlertType.METRIC_SPIKE,
        message=message,
        metadata={
            "metric_key": key,
            "old_value": baseline.value,
            "new_value": latest.value,
            "delta_pct": round(pct_change * 100, 2),
            "threshold_pct": round(threshold_pct * 100, 2),
            "severity": _compute_severity(abs(pct_change), threshold_pct),
            "baseline_captured_at": baseline.captured_at.isoformat(),
            "latest_captured_at": latest.captured_at.isoformat(),
        },
    )
    logger.info(
        "Alert created: entity_id=%d key=%s delta=%s",
        entity_id,
        key,
        pct_display,
    )


def run_compound_detection() -> int:
    """
    Evaluate all active CompoundRules against current metric deltas for every entity.

    For each entity, computes the pct_change for every metric key that has a
    DeltaThreshold configured, then checks each active CompoundRule. If a rule
    fires, a COMPOUND_SPIKE alert is created with metadata listing every
    triggered condition and its actual delta.

    Deduplication: skips if an alert with the same rule_id + entity already
    exists within the rule's lookback window.

    Returns the number of alerts created.
    """

    rules = list(CompoundRule.objects.filter(is_active=True))
    if not rules:
        return 0

    # Load thresholds for lookback_days per metric key
    thresholds: dict[str, DeltaThreshold] = {
        dt.metric_key: dt for dt in DeltaThreshold.objects.all()
    }

    now = timezone.now()
    alerts_created = 0

    # Find all entities that have any metric data
    entity_ids = list(
        Metric.objects.order_by("entity_id").values_list("entity_id", flat=True).distinct()
    )

    for entity_id in entity_ids:
        # Build delta snapshot: {metric_key: pct_change} for this entity
        deltas = _build_entity_deltas(entity_id, thresholds, now)
        if not deltas:
            continue

        for rule in rules:
            # Evaluate the rule against this entity's deltas
            conditions = rule.conditions
            if not isinstance(conditions, list) or not conditions:
                continue

            all_met = True
            triggered_conditions: list[dict] = []

            for cond in conditions:
                key = cond.get("metric_key", "")
                operator = cond.get("operator", "gte")
                threshold_pct = cond.get("threshold_pct", 0.0)

                delta = deltas.get(key)
                if delta is None:
                    all_met = False
                    break

                # Check if condition is met
                if operator == "gte" and delta >= threshold_pct:
                    triggered_conditions.append(
                        {
                            "metric_key": key,
                            "operator": operator,
                            "threshold_pct": round(threshold_pct * 100, 2),
                            "actual_delta_pct": round(delta * 100, 2),
                        }
                    )
                elif operator == "lte" and delta <= -threshold_pct:
                    triggered_conditions.append(
                        {
                            "metric_key": key,
                            "operator": operator,
                            "threshold_pct": round(threshold_pct * 100, 2),
                            "actual_delta_pct": round(delta * 100, 2),
                        }
                    )
                else:
                    all_met = False
                    break

            if not all_met:
                continue

            # Deduplication: check if this rule already fired for this entity recently
            lookback = max(
                (
                    thresholds[c["metric_key"]].lookback_days
                    for c in conditions
                    if c.get("metric_key") in thresholds
                ),
                default=7,
            )
            dedup_cutoff = now - timezone.timedelta(days=lookback)
            already_exists = Alert.objects.filter(
                entity_id=entity_id,
                alert_type=Alert.AlertType.COMPOUND_SPIKE,
                metadata__rule_id=rule.pk,
                created_at__gte=dedup_cutoff,
            ).exists()

            if already_exists:
                continue

            # Compute severity from the max individual condition severity
            max_severity = "LOW"
            for tc in triggered_conditions:
                key = tc["metric_key"]
                dt = thresholds.get(key)
                if dt:
                    sev = _compute_severity(abs(tc["actual_delta_pct"]) / 100, dt.threshold_pct)
                    if sev == "HIGH" or (sev == "MEDIUM" and max_severity != "HIGH"):
                        max_severity = sev

            # Build human-readable message
            parts = []
            for tc in triggered_conditions:
                direction = "up" if tc["actual_delta_pct"] > 0 else "down"
                parts.append(f"{tc['metric_key']} {direction} {abs(tc['actual_delta_pct']):.1f}%")
            message = f"Compound pattern: {' + '.join(parts)}"

            Alert.objects.create(
                entity_id=entity_id,
                alert_type=Alert.AlertType.COMPOUND_SPIKE,
                message=message,
                metadata={
                    "rule_id": rule.pk,
                    "rule_name": rule.name,
                    "conditions": triggered_conditions,
                    "fitness_score": rule.fitness_score,
                    "severity": max_severity,
                },
            )
            logger.info(
                "COMPOUND_SPIKE alert: entity_id=%d rule=%d (%s)",
                entity_id,
                rule.pk,
                rule.name,
            )
            alerts_created += 1

    logger.info("Compound detection: created %d alert(s).", alerts_created)
    return alerts_created


def _build_entity_deltas(
    entity_id: int,
    thresholds: dict[str, DeltaThreshold],
    now,
) -> dict[str, float]:
    """
    Compute current pct_change for all configured metric keys for one entity.

    Uses the same baseline logic as run_delta_detection: latest value vs.
    most recent value from >= lookback_days ago.
    """
    deltas: dict[str, float] = {}

    for key, dt in thresholds.items():
        cutoff = now - timezone.timedelta(days=dt.lookback_days)

        latest = (
            Metric.objects.filter(entity_id=entity_id, key=key).order_by("-captured_at").first()
        )
        if latest is None or latest.value is None:
            continue

        baseline = (
            Metric.objects.filter(entity_id=entity_id, key=key, captured_at__lte=cutoff)
            .order_by("-captured_at")
            .first()
        )
        if baseline is None or baseline.value is None or baseline.value == 0:
            continue

        deltas[key] = (latest.value - baseline.value) / baseline.value

    return deltas


# ---------------------------------------------------------------------------
# Competitor candidate detection (LLM-first with article scraping)
# ---------------------------------------------------------------------------

# Fire COMPETITOR_NEW alert after this many mentions
COMPETITOR_MENTION_THRESHOLD = 3

# Max sample URLs to store per candidate
MAX_SAMPLE_URLS = 5

# Minimum LLM relevance score (1-10) to store a candidate
MIN_RELEVANCE_SCORE = 6

# Categories considered competitive (others like "media", "other" are discarded)
COMPETITIVE_CATEGORIES = frozenset(
    {"competitor", "exchange", "wallet", "protocol", "infrastructure"}
)

# Rough chars-per-token estimate for context budgeting
_CHARS_PER_TOKEN = 4

# Max articles per LLM batch (prevents output token exhaustion even if
# the context budget has room — the LLM must produce JSON for each article)
_MAX_ARTICLES_PER_BATCH = 50

# Token overhead reserved for system prompt + entity list
_SYSTEM_PROMPT_OVERHEAD = 2_000

# System prompt for LLM-based competitor extraction (with entity list placeholder)
_COMPETITOR_SYSTEM_PROMPT = """\
You are a competitive intelligence analyst for {org_name}, {org_description}.
You are analyzing news articles to identify companies, products, protocols, or organisations \
that could be competitors or relevant entities in the Bitcoin, cryptocurrency, blockchain, \
and fintech space.

Currently tracked entities (DO NOT extract these — they are already monitored):
{tracked_entities}

For each article, extract any company, product, protocol, or organisation names that are \
NOT in the tracked list above.

For each extracted entity provide:
- name: the canonical company or product name (clean, no trailing words like "Inc" or "Ltd")
- category: one of "competitor", "exchange", "wallet", "protocol", "infrastructure", \
  "media", "other"
- relevance: integer 1-10 indicating competitive relevance to {org_name} \
  (10 = direct competitor in Bitcoin wallets/mining/sidechains, 1 = barely related)
- reason: one-sentence explanation of why this entity matters competitively

Rules:
- Do NOT extract generic terms, market concepts, attack names, place names, or people's names.
- Do NOT extract entities already in the tracked list above.
- Do NOT extract media outlets, news sites, or publications.
- If an article mentions no new entities worth tracking, return an empty entities array.
- Respond ONLY with valid JSON matching the schema below. No markdown fences, no explanation.

Response schema:
[
  {{
    "article_index": 0,
    "entities": [
      {{"name": "Example Corp", "category": "competitor", "relevance": 7, "reason": "..."}}
    ]
  }}
]\
"""


def _estimate_tokens(text: str) -> int:
    """Estimate token count from character length."""
    return len(text) // _CHARS_PER_TOKEN


def _get_context_budget() -> int:
    """
    Return the token budget for article content based on which LLM backend is active.

    Checks Ollama availability first (matching LLMClient.chat fallback order).
    Falls back to GROQ_CONTEXT_BUDGET.
    """
    from django.conf import settings

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
    Batches are sized so the combined token estimate stays under
    *context_budget* minus system prompt overhead.

    An article that exceeds the remaining budget on its own starts a new batch.
    """
    usable = max(context_budget - _SYSTEM_PROMPT_OVERHEAD, 4_000)
    batches: list[list[tuple[int, str, str, str]]] = []
    current_batch: list[tuple[int, str, str, str]] = []
    current_tokens = 0

    for article in articles:
        _, title, content, _ = article
        article_tokens = _estimate_tokens(title) + _estimate_tokens(content) + 20  # formatting
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


def _llm_extract_competitors(
    client: object,
    batch: list[tuple[int, str, str, str]],
    tracked_names: set[str],
) -> list[dict]:
    """
    Send a batch of articles to the LLM and return extracted competitor entities.

    Each article tuple is ``(newsitem_pk, title, content_excerpt, url)``.

    Returns a list of dicts with keys:
    ``name``, ``category``, ``relevance``, ``reason``, ``article_url``.

    Non-fatal: returns empty list on LLM failure.
    """
    import json

    # Build the tracked-entities list for the system prompt
    tracked_list = "\n".join(f"- {n}" for n in sorted(tracked_names))
    system = _COMPETITOR_SYSTEM_PROMPT.format(
        tracked_entities=tracked_list,
        org_name=settings.PRIMARY_ORG_NAME,
        org_description=settings.PRIMARY_ORG_DESCRIPTION,
    )

    # Build numbered article list for the user prompt
    lines = [f"Analyze these {len(batch)} articles and extract competitor entities:\n"]
    for idx, (_, title, content, _) in enumerate(batch):
        lines.append(f"[Article {idx}]")
        lines.append(f"Title: {title}")
        if content:
            lines.append(f"Content: {content}")
        lines.append("")
    user_prompt = "\n".join(lines)

    try:
        raw = client.chat(system=system, user=user_prompt, timeout=90)
    except Exception as exc:
        logger.warning("LLM competitor extraction failed: %s", exc)
        return []

    # Parse JSON — strip markdown fences if present
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        try:
            first_nl = cleaned.index("\n")
            cleaned = cleaned[first_nl + 1 :]
        except ValueError:
            pass
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].strip()

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.warning("LLM returned invalid JSON for competitor extraction: %s", exc)
        return []

    if not isinstance(result, list):
        logger.warning("LLM returned non-list for competitor extraction.")
        return []

    # Flatten and validate
    tracked_lower = {n.lower() for n in tracked_names}
    entities: list[dict] = []
    for article_result in result:
        if not isinstance(article_result, dict):
            continue
        article_idx = article_result.get("article_index", -1)
        article_url = ""
        if isinstance(article_idx, int) and 0 <= article_idx < len(batch):
            article_url = batch[article_idx][3]  # url from tuple

        for ent in article_result.get("entities", []):
            if not isinstance(ent, dict):
                continue
            name = ent.get("name", "").strip()
            if not name or len(name) < 3:
                continue
            # Hallucination guard: skip names matching tracked entities
            if name.lower() in tracked_lower:
                continue
            entities.append(
                {
                    "name": name,
                    "category": str(ent.get("category", "other")).lower(),
                    "relevance": int(ent.get("relevance", 0)),
                    "reason": str(ent.get("reason", "")),
                    "article_url": article_url,
                }
            )

    logger.info(
        "LLM competitor extraction: batch of %d articles → %d entities extracted.",
        len(batch),
        len(entities),
    )
    return entities


def run_competitor_detection() -> int:
    """
    Analyze recent news articles with an LLM to extract competitor entities.

    Pipeline:
    1. Scrape article content (cached on NewsItem) for richer context.
    2. Build context-window-aware batches of articles (title + content).
    3. Send each batch to the LLM for structured entity extraction.
    4. Filter results by category and relevance score.
    5. Upsert CandidateEntity rows with LLM-provided metadata.
    6. Fire COMPETITOR_NEW alerts when mention threshold is reached.

    Non-fatal: if the LLM is unavailable, logs a warning and returns 0.
    No regex fallback — the LLM is the sole extraction engine.

    Returns the number of alerts created.
    """
    from core.models import Entity as EntityModel
    from metrics.models import CandidateEntity, NewsItem
    from metrics.scraper import scrape_article, truncate_to_words

    # Collect tracked entity names for exclusion and prompt context
    tracked_names: set[str] = set(EntityModel.objects.values_list("name", flat=True))
    tracked_lower: set[str] = {n.lower() for n in tracked_names}

    # Also exclude dismissed candidates
    dismissed_lower: set[str] = {
        n.lower()
        for n in CandidateEntity.objects.filter(
            status=CandidateEntity.Status.DISMISSED
        ).values_list("name", flat=True)
    }

    # Initialise LLM client — if unavailable, skip entirely
    try:
        from metrics.llm_review import LLMClient

        client = LLMClient.from_settings()
    except Exception as exc:
        logger.warning("Competitor detection: LLM unavailable, skipping. %s", exc)
        return 0

    # Fetch recent news articles (last 24 hours)
    cutoff = timezone.now() - timezone.timedelta(hours=24)
    recent_news = list(NewsItem.objects.filter(fetched_at__gte=cutoff))

    if not recent_news:
        logger.info("Competitor detection: no recent news articles.")
        return 0

    # Scrape article content (cached — only fetches if not already scraped)
    articles: list[tuple[int, str, str, str]] = []
    for item in recent_news:
        body = scrape_article(item)
        excerpt = truncate_to_words(body) if body else ""
        articles.append((item.pk, item.title, excerpt, item.url))

    logger.info("Competitor detection: processing %d articles.", len(articles))

    # Build batches respecting context window limits
    context_budget = _get_context_budget()
    batches = _build_article_batches(articles, context_budget)
    logger.info(
        "Competitor detection: %d articles split into %d batch(es) (budget=%d tokens).",
        len(articles),
        len(batches),
        context_budget,
    )

    # Extract competitors from each batch
    all_extracted: list[dict] = []
    for batch in batches:
        extracted = _llm_extract_competitors(client, batch, tracked_names)
        all_extracted.extend(extracted)

    if not all_extracted:
        logger.info("Competitor detection: LLM extracted no competitor entities.")
        return 0

    # Aggregate by name (case-insensitive): merge mentions, keep best relevance
    # key_to_name maps lowercase → best canonical name (highest relevance wins)
    aggregated: dict[str, dict] = {}
    key_to_name: dict[str, str] = {}
    for ent in all_extracted:
        name = ent["name"]
        key = name.lower()
        # Skip tracked entities and dismissed candidates
        if key in tracked_lower or key in dismissed_lower:
            continue
        # Skip non-competitive categories
        if ent["category"] not in COMPETITIVE_CATEGORIES:
            continue
        # Skip low relevance
        if ent["relevance"] < MIN_RELEVANCE_SCORE:
            continue

        if key in aggregated:
            existing = aggregated[key]
            if ent["article_url"] and len(existing["urls"]) < MAX_SAMPLE_URLS:
                if ent["article_url"] not in existing["urls"]:
                    existing["urls"].append(ent["article_url"])
            # Keep highest relevance score, its reason, and its canonical name
            if ent["relevance"] > existing["relevance"]:
                existing["relevance"] = ent["relevance"]
                existing["reason"] = ent["reason"]
                existing["category"] = ent["category"]
                key_to_name[key] = name
        else:
            aggregated[key] = {
                "category": ent["category"],
                "relevance": ent["relevance"],
                "reason": ent["reason"],
                "urls": [ent["article_url"]] if ent["article_url"] else [],
            }
            key_to_name[key] = name

    logger.info(
        "Competitor detection: %d raw extractions → %d unique candidates after filtering.",
        len(all_extracted),
        len(aggregated),
    )

    # Upsert CandidateEntity rows and fire alerts
    alerts_created = 0
    for key, data in aggregated.items():
        name = key_to_name[key]
        candidate, created = CandidateEntity.objects.get_or_create(
            name=name,
            defaults={
                "sample_urls": data["urls"],
                "category": data["category"],
                "relevance_score": data["relevance"],
                "llm_reason": data["reason"],
            },
        )
        if not created:
            candidate.mention_count += 1
            # Append new URLs up to limit
            existing_urls = candidate.sample_urls or []
            for url in data["urls"]:
                if url not in existing_urls and len(existing_urls) < MAX_SAMPLE_URLS:
                    existing_urls.append(url)
            candidate.sample_urls = existing_urls
            # Keep highest relevance score
            if data["relevance"] > (candidate.relevance_score or 0):
                candidate.relevance_score = data["relevance"]
                candidate.llm_reason = data["reason"]
            # Set category if previously empty
            if not candidate.category:
                candidate.category = data["category"]
            candidate.save(
                update_fields=[
                    "mention_count",
                    "last_seen",
                    "sample_urls",
                    "relevance_score",
                    "llm_reason",
                    "category",
                ]
            )

        # Fire alert if threshold reached and not already fired
        if candidate.mention_count >= COMPETITOR_MENTION_THRESHOLD and not candidate.alert_fired:
            primary = EntityModel.objects.filter(is_primary=True).first()
            if primary:
                Alert.objects.create(
                    entity=primary,
                    alert_type=Alert.AlertType.COMPETITOR_NEW,
                    message=(
                        f"New competitor candidate detected: {name} "
                        f"({candidate.mention_count} mentions, "
                        f"relevance {candidate.relevance_score}/10)"
                    ),
                    metadata={
                        "candidate_name": name,
                        "candidate_id": candidate.pk,
                        "mention_count": candidate.mention_count,
                        "category": candidate.category,
                        "relevance_score": candidate.relevance_score,
                        "sample_urls": candidate.sample_urls,
                    },
                )
                candidate.alert_fired = True
                candidate.save(update_fields=["alert_fired"])
                alerts_created += 1

    logger.info("Competitor detection: created %d alert(s).", alerts_created)
    return alerts_created

# metrics/llm_review.py
"""
LLM-powered data quality review for anomaly detection training.

LLMClient        — Ollama-first, Groq API fallback. Self-contained; no
                   imports from briefings/ to avoid cross-app coupling.

AnomalyLLMReviewer — Uses LLMClient to:
  1. Review training data distributions before fitting Isolation Forest.
  2. Suggest cold-start thresholds for entities with insufficient history.

Design constraints:
  - LLM is called ONLY during the monthly train_anomaly_models command,
    never at detection time, keeping the hot path deterministic.
  - All LLM failures are non-fatal: callers always receive a safe default.
  - Groq uses the OpenAI-compatible /v1/chat/completions endpoint.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Literal

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Response dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrainingReview:
    """Result of LLM review of a training data distribution."""

    verdict: Literal["proceed", "skip", "warn"]
    reason: str
    recommended_contamination: float


@dataclass(frozen=True)
class ThresholdSuggestion:
    """LLM-suggested threshold for entities with insufficient training data."""

    threshold_pct: float
    reason: str


# ---------------------------------------------------------------------------
# LLMClient — Ollama-first, Groq fallback
# ---------------------------------------------------------------------------

_GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"


class LLMClient:
    """
    Thin LLM client with automatic Ollama-to-Groq fallback.

    Tries the local Ollama service first. When Ollama is unreachable and a
    Groq API key is configured, falls back to the Groq cloud endpoint
    (OpenAI-compatible format).
    """

    def __init__(
        self,
        ollama_base_url: str | None = None,
        ollama_model: str | None = None,
        groq_api_key: str | None = None,
        groq_model: str | None = None,
    ) -> None:
        """
        Initialise with Ollama and/or Groq credentials.

        When no arguments are provided, reads from Django settings.
        """
        self.ollama_base_url = (ollama_base_url or settings.OLLAMA_BASE_URL).rstrip("/")
        self.ollama_model = ollama_model or settings.OLLAMA_MODEL
        self.groq_api_key = groq_api_key if groq_api_key is not None else settings.GROQ_API_KEY
        self.groq_model = groq_model or settings.GROQ_MODEL
        self._ollama_ok: bool | None = None  # cached after first probe

    def is_ollama_available(self) -> bool:
        """Probe Ollama with a lightweight /api/tags request."""
        try:
            resp = requests.get(f"{self.ollama_base_url}/api/tags", timeout=5)
            return resp.status_code == 200
        except requests.RequestException as exc:
            logger.debug("Ollama not available: %s", exc)
            return False

    def chat(self, system: str, user: str, timeout: int = 120) -> str:
        """
        Send a chat request and return the model response text.

        Probes Ollama once per client instance (cached). Falls back to Groq
        if Ollama is unreachable and GROQ_API_KEY is configured.

        Raises:
            RuntimeError: Both backends are unavailable.
            requests.HTTPError: Non-2xx response from the chosen backend.
        """
        if self._ollama_ok is None:
            self._ollama_ok = self.is_ollama_available()

        if self._ollama_ok:
            try:
                return self._chat_ollama(system, user, timeout)
            except requests.ConnectionError:
                # Ollama went away mid-session; invalidate cache and try fallback
                self._ollama_ok = False

        if self.groq_api_key:
            logger.info("Ollama unavailable — falling back to Groq (%s).", self.groq_model)
            return self._chat_groq(system, user, timeout)

        raise RuntimeError(
            "No LLM backend available: Ollama is unreachable and GROQ_API_KEY is not set."
        )

    @classmethod
    def from_settings(cls) -> LLMClient:
        """Construct a client from Django settings."""
        return cls(
            ollama_base_url=settings.OLLAMA_BASE_URL,
            ollama_model=settings.OLLAMA_MODEL,
            groq_api_key=settings.GROQ_API_KEY,
            groq_model=settings.GROQ_MODEL,
        )

    # -- Private backend methods ------------------------------------------

    def _chat_ollama(self, system: str, user: str, timeout: int) -> str:
        """Call the Ollama /api/chat endpoint."""
        payload = {
            "model": self.ollama_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
        }
        resp = requests.post(
            f"{self.ollama_base_url}/api/chat",
            json=payload,
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"]

    def _chat_groq(self, system: str, user: str, timeout: int) -> str:
        """Call the Groq OpenAI-compatible /v1/chat/completions endpoint."""
        payload = {
            "model": self.groq_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        resp = requests.post(
            _GROQ_CHAT_URL,
            headers={
                "Authorization": f"Bearer {self.groq_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# AnomalyLLMReviewer
# ---------------------------------------------------------------------------

_REVIEW_SYSTEM_PROMPT = """\
You are a data quality analyst for a competitive intelligence anomaly detection system.
Your task is to evaluate whether a distribution of metric percent-changes is suitable
for training an Isolation Forest anomaly detection model.
Respond ONLY with valid JSON matching the requested schema. No preamble, no markdown.\
"""

_COLD_START_SYSTEM_PROMPT = """\
You are a competitive intelligence analyst calibrating anomaly detection thresholds.
Given an entity's profile and vertical, suggest an appropriate anomaly threshold
(as a fractional percentage) for a metric that lacks sufficient historical data.
Respond ONLY with valid JSON matching the requested schema. No preamble, no markdown.\
"""

DEFAULT_CONTAMINATION = 0.05


class AnomalyLLMReviewer:
    """
    Uses an LLM to review training data quality and suggest cold-start thresholds.

    All methods are non-fatal: LLM unavailability or malformed responses produce
    safe defaults (proceed with default contamination, or None for cold-start).
    """

    def __init__(self, client: LLMClient | None = None) -> None:
        """Allow injecting a pre-configured client (useful for tests)."""
        self.client = client or LLMClient.from_settings()

    def review_training_data(
        self,
        entity_slug: str,
        metric_key: str,
        n: int,
        mean_change: float,
        std_change: float,
        p5: float,
        p95: float,
        span_days: int,
        pct_monotonic: float,
    ) -> TrainingReview:
        """
        Ask LLM whether a distribution is suitable for Isolation Forest training.

        Args:
            entity_slug: Entity identifier for context.
            metric_key: Metric being evaluated (e.g. 'github_stars').
            n: Number of pct_change samples.
            mean_change: Mean of the pct_changes (fractional).
            std_change: Standard deviation of the pct_changes (fractional).
            p5: 5th percentile of the pct_changes.
            p95: 95th percentile of the pct_changes.
            span_days: Calendar days between first and last data point.
            pct_monotonic: Fraction of changes in the dominant direction.

        Returns:
            TrainingReview with verdict, reason, and recommended contamination.
            On any failure: returns proceed with default contamination.
        """
        user_prompt = (
            f"Review this distribution for Isolation Forest suitability:\n"
            f"  Entity: {entity_slug} | Metric: {metric_key}\n"
            f"  N samples: {n} | Time span: {span_days} days\n"
            f"  Mean change: {mean_change:+.4f} | Std: {std_change:.4f}\n"
            f"  P5: {p5:+.4f} | P95: {p95:+.4f}\n"
            f"  Same-direction fraction: {pct_monotonic:.0%}\n\n"
            f"Flags to check:\n"
            f"- Is std near zero? (flat-lined metric — model would be useless)\n"
            f"- Is same-direction fraction >90%? (strongly monotonic — IF will "
            f"flag all future movement as anomalous)\n"
            f"- Is span < 30 days? (too dense, not representative of real variance)\n\n"
            f'Respond: {{"verdict": "proceed"|"skip"|"warn", "reason": "...", '
            f'"recommended_contamination": 0.05}}'
        )

        try:
            raw = self.client.chat(system=_REVIEW_SYSTEM_PROMPT, user=user_prompt)
            data = json.loads(self._extract_json(raw))
            verdict = data.get("verdict", "proceed")
            if verdict not in ("proceed", "skip", "warn"):
                verdict = "proceed"
            return TrainingReview(
                verdict=verdict,
                reason=data.get("reason", ""),
                recommended_contamination=float(
                    data.get("recommended_contamination", DEFAULT_CONTAMINATION)
                ),
            )
        except (RuntimeError, requests.RequestException) as exc:
            logger.warning("LLM unavailable for training review: %s", exc)
            return TrainingReview(
                verdict="proceed",
                reason="llm_unavailable",
                recommended_contamination=DEFAULT_CONTAMINATION,
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.warning("LLM returned unparseable response for review: %s", exc)
            return TrainingReview(
                verdict="proceed",
                reason="parse_error",
                recommended_contamination=DEFAULT_CONTAMINATION,
            )

    def suggest_cold_start_threshold(
        self,
        entity_slug: str,
        metric_key: str,
        vertical: str,
        n_available: int,
        min_points: int,
    ) -> ThresholdSuggestion | None:
        """
        Suggest a threshold for an entity with insufficient training data.

        Args:
            entity_slug: Entity identifier.
            metric_key: Metric key (e.g. 'github_stars').
            vertical: Entity's vertical category (e.g. 'Wallet (HW)').
            n_available: Number of data points currently available.
            min_points: Minimum required for full model training.

        Returns:
            ThresholdSuggestion with threshold_pct and reason, or None on failure.
        """
        user_prompt = (
            f"Entity '{entity_slug}' (vertical: {vertical}) has only {n_available} "
            f"data points for '{metric_key}' — insufficient for statistical model "
            f"training (need {min_points}).\n\n"
            f"Based on the entity profile and vertical category, suggest an "
            f"appropriate anomaly detection threshold as a fractional percentage:\n"
            f"- Higher threshold (e.g. 0.30) for small/volatile entities\n"
            f"- Lower threshold (e.g. 0.10) for large/stable entities\n\n"
            f'Respond: {{"threshold_pct": 0.25, "reason": "..."}}'
        )

        try:
            raw = self.client.chat(system=_COLD_START_SYSTEM_PROMPT, user=user_prompt)
            data = json.loads(self._extract_json(raw))
            threshold = float(data["threshold_pct"])
            if threshold <= 0 or threshold > 1.0:
                logger.warning(
                    "LLM suggested invalid threshold %.4f for %s/%s; ignoring.",
                    threshold,
                    entity_slug,
                    metric_key,
                )
                return None
            return ThresholdSuggestion(
                threshold_pct=threshold,
                reason=data.get("reason", ""),
            )
        except (RuntimeError, requests.RequestException) as exc:
            logger.warning("LLM unavailable for cold-start suggestion: %s", exc)
            return None
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.warning("LLM returned unparseable cold-start response: %s", exc)
            return None

    @staticmethod
    def _extract_json(raw: str) -> str:
        """
        Extract JSON object from LLM response that may contain markdown fences.

        Handles responses wrapped in ```json ... ``` blocks.
        """
        stripped = raw.strip()
        if stripped.startswith("```"):
            # Remove opening fence (possibly with language tag)
            first_newline = stripped.index("\n")
            stripped = stripped[first_newline + 1 :]
            # Remove closing fence
            if stripped.endswith("```"):
                stripped = stripped[:-3].strip()
        return stripped

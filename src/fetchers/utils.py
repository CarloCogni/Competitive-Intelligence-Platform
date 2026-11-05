# fetchers/utils.py
"""Shared utility helpers for all fetchers."""

import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

logger = logging.getLogger(__name__)


def parse_date(value: str | None) -> datetime | None:
    """
    Parse a date string into an aware UTC datetime.

    Handles ISO-8601 strings (GitHub API) and RFC-2822 strings (RSS feeds).
    Returns None on failure so callers can guard without crashing.
    """
    if not value:
        return None

    # Try ISO-8601 (e.g. "2024-03-15T12:00:00Z")
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(value, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue

    # Try RFC-2822 (e.g. RSS pubDate: "Mon, 01 Jan 2024 00:00:00 +0000")
    try:
        return parsedate_to_datetime(value)
    except Exception:
        pass

    logger.debug("Could not parse date: %r", value)
    return None


def to_float(value: Any) -> float | None:
    """Safely cast a value to float; return None if conversion fails."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

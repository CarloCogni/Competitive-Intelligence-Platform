# dashboard/services.py
"""Service functions for the dashboard application."""

import logging
from typing import Iterable

from core.models import Entity, UserProfile

logger = logging.getLogger(__name__)


def save_profile_settings(
    profile: UserProfile,
    entities: Iterable[Entity],
    digest_enabled: bool,
    timezone: str,
) -> None:
    """
    Persist user profile settings atomically.

    Uses update_fields to avoid a full-row write on the profile,
    and M2M .set() to atomically replace the watchlist without
    creating a momentary empty-watchlist window.
    """
    profile.digest_enabled = digest_enabled
    profile.timezone = timezone
    profile.save(update_fields=["digest_enabled", "timezone"])
    profile.watched_entities.set(entities)
    logger.debug(
        "Saved profile settings for user=%s digest=%s timezone=%s watched_count=%d",
        profile.user_id,
        digest_enabled,
        timezone,
        profile.watched_entities.count(),
    )

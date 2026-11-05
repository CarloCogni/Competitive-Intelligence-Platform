# core/signals.py
"""
Django signals for the core app.

Auto-creates a UserProfile whenever a new User is saved for the first time,
so every authenticated user always has a profile without manual setup.
"""

import logging

from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)

User = get_user_model()


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created: bool, **kwargs) -> None:
    """Create a UserProfile when a new User is first saved."""
    if not created:
        return
    # Import here to avoid circular imports at module load time
    from core.models import UserProfile

    profile, _ = UserProfile.objects.get_or_create(user=instance)
    # Seed the watchlist with all current entities so a new user sees
    # the full dashboard by default and can then trim to their interests.
    try:
        from core.models import Entity

        profile.watched_entities.set(Entity.objects.all())
    except Exception:
        # Entities table may not exist yet (e.g. fresh migration run)
        logger.debug("Could not seed watched_entities for user=%s", instance.username)
    logger.debug("Created UserProfile for user=%s", instance.username)

# core/models.py
"""Core models: Vertical (product category), Entity (tracked organisation), and UserProfile."""

import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.text import slugify

# Limits for keyword JSONFields to prevent abuse via excessively long queries.
_MAX_KEYWORDS = 10
_MAX_KEYWORD_LENGTH = 100


def validate_keyword_list(value: object) -> None:
    """Ensure *value* is a list of strings within size limits."""
    if not isinstance(value, list):
        raise ValidationError("Must be a list of strings.")
    if len(value) > _MAX_KEYWORDS:
        raise ValidationError(f"At most {_MAX_KEYWORDS} keywords allowed.")
    for item in value:
        if not isinstance(item, str):
            raise ValidationError("Each keyword must be a string.")
        if len(item) > _MAX_KEYWORD_LENGTH:
            raise ValidationError(f"Each keyword must be at most {_MAX_KEYWORD_LENGTH} characters.")


logger = logging.getLogger(__name__)


class Vertical(models.Model):
    """A product category / market vertical (e.g. 'Wallet (HW)', 'Sidechain / L2')."""

    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)

    class Meta:
        verbose_name = "Vertical"
        verbose_name_plural = "Verticals"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs) -> None:
        """Auto-populate slug from name if not set."""
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class Entity(models.Model):
    """A tracked organisation or product (one of your own, or a competitor)."""

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, unique=True)
    vertical = models.ForeignKey(
        Vertical,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="entities",
    )
    # True = one of the primary organisation's products (settings.PRIMARY_ORG_NAME)
    is_primary = models.BooleanField(
        default=False,
        help_text="True for your own organisation's products; False for competitors.",
    )

    # --- API identifiers (blank = fetcher skips this entity) ---
    github_org = models.CharField(
        max_length=200,
        blank=True,
        help_text="GitHub organisation name (e.g. 'Blockstream').",
    )
    github_repo = models.CharField(
        max_length=200,
        blank=True,
        help_text="Primary GitHub repository slug (e.g. 'green_qt').",
    )
    app_store_id = models.CharField(
        max_length=100,
        blank=True,
        help_text="iTunes App Store numeric ID.",
    )
    news_keywords = models.JSONField(
        default=list,
        blank=True,
        validators=[validate_keyword_list],
        help_text="List of keywords used to search Google News RSS.",
    )
    twitter_handle = models.CharField(
        max_length=100,
        blank=True,
        help_text="Twitter/X handle without '@' (e.g. 'Blockstream').",
    )
    reddit_keywords = models.JSONField(
        default=list,
        blank=True,
        validators=[validate_keyword_list],
        help_text=(
            "List of keywords to search on Reddit (e.g. ['Blockstream "
            "Jade', 'Jade wallet'])."
        ),
    )
    play_store_id = models.CharField(
        max_length=200,
        blank=True,
        help_text=(
            "Google Play Store package name (e.g. "
            "'com.greenaddress.greenbits_android_wallet')."
        ),
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Entity"
        verbose_name_plural = "Entities"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["slug"]),
            models.Index(fields=["vertical", "is_primary"]),
        ]

    def __str__(self) -> str:
        return self.name


class UserProfile(models.Model):
    """Extended profile for each Django user: watchlist and digest preferences."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    # Entities this user has pinned to their personal watchlist
    watched_entities = models.ManyToManyField(
        Entity,
        blank=True,
        related_name="watchers",
    )
    # Whether this user receives weekly digest emails
    digest_enabled = models.BooleanField(default=True)
    # When True, all views show every entity instead of the user's watchlist only
    show_all_entities = models.BooleanField(
        default=False,
        help_text="When True, all views show all entities instead of watchlist only.",
    )
    # IANA timezone name for display purposes (e.g. 'America/New_York')
    timezone = models.CharField(
        max_length=100,
        default="America/Mexico_City",
        help_text="IANA timezone name (e.g. 'America/New_York').",
    )

    class Meta:
        verbose_name = "User Profile"
        verbose_name_plural = "User Profiles"

    def __str__(self) -> str:
        return f"Profile({self.user.username})"

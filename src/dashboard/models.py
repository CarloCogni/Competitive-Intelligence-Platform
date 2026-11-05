# dashboard/models.py
"""Dashboard-specific models: saved head-to-head comparisons per user."""

import logging

from django.conf import settings
from django.db import models

from core.models import Entity

logger = logging.getLogger(__name__)


class SavedComparison(models.Model):
    """
    A named head-to-head entity pair bookmarked by a user.

    Allows users to quickly reload a recurring comparison (e.g. 'Jade vs Coldcard')
    without re-selecting both dropdowns every visit.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="saved_comparisons",
    )
    name = models.CharField(max_length=100)
    entity_a = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name="+")
    entity_b = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Saved Comparison"
        verbose_name_plural = "Saved Comparisons"
        unique_together = [("user", "name")]
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.user} — {self.entity_a} vs {self.entity_b} ({self.name})"

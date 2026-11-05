# dashboard/admin.py
"""Django admin configuration for dashboard models."""

from django.contrib import admin

from .models import SavedComparison


@admin.register(SavedComparison)
class SavedComparisonAdmin(admin.ModelAdmin):
    """Admin for SavedComparison — user-bookmarked head-to-head comparisons."""

    list_display = ("user", "name", "entity_a", "entity_b", "created_at")
    list_filter = ("user",)
    search_fields = ("name", "entity_a__name", "entity_b__name")
    readonly_fields = ("created_at",)
    date_hierarchy = "created_at"

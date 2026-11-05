# core/admin.py
"""Django admin configuration for core models."""

from django.contrib import admin

from .models import Entity, UserProfile, Vertical


@admin.register(Vertical)
class VerticalAdmin(admin.ModelAdmin):
    """Admin for Vertical."""

    list_display = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Entity)
class EntityAdmin(admin.ModelAdmin):
    """Admin for Entity."""

    list_display = ("name", "slug", "vertical", "is_primary", "github_org", "app_store_id")
    list_filter = ("vertical", "is_primary")
    search_fields = ("name", "slug", "github_org")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    """Admin for UserProfile."""

    list_display = ("user", "digest_enabled", "get_watched_entities_count")
    list_filter = ("digest_enabled",)
    search_fields = ("user__username", "user__email")
    filter_horizontal = ("watched_entities",)
    list_select_related = ("user",)

    @admin.display(description="Watched Entities")
    def get_watched_entities_count(self, obj: UserProfile) -> int:
        """Return the count of watched entities for the list display."""
        return obj.watched_entities.count()

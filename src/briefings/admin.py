# briefings/admin.py
"""Admin registration for the briefings app."""

from django import forms
from django.contrib import admin

from .models import BriefingConfig, EmailConfig, WeeklyBriefing


@admin.register(WeeklyBriefing)
class WeeklyBriefingAdmin(admin.ModelAdmin):
    """Admin view for generated briefings."""

    list_display = ("__str__", "status", "model_used", "alert_count", "entity_count", "created_at")
    list_filter = ("status", "model_used")
    readonly_fields = (
        "content",
        "status",
        "model_used",
        "generated_at",
        "error_message",
        "entity_count",
        "alert_count",
        "news_count",
        "created_at",
    )
    ordering = ("-period_end",)


class BriefingConfigForm(forms.ModelForm):
    """Form that masks the Groq API key in the admin."""

    class Meta:
        model = BriefingConfig
        fields = "__all__"
        widgets = {"groq_api_key": forms.PasswordInput(render_value=True)}


@admin.register(BriefingConfig)
class BriefingConfigAdmin(admin.ModelAdmin):
    """Admin view for the singleton briefing configuration."""

    form = BriefingConfigForm
    list_display = (
        "__str__",
        "llm_provider",
        "ollama_model",
        "groq_model",
        "period_days",
        "send_briefing_email",
    )

    def has_add_permission(self, request) -> bool:  # type: ignore[override]
        """Only one BriefingConfig row is allowed."""
        return not BriefingConfig.objects.exists()

    def has_delete_permission(self, request, obj=None) -> bool:  # type: ignore[override]
        """Prevent deletion of the singleton."""
        return False


class EmailConfigForm(forms.ModelForm):
    """Form that masks the SMTP password in the admin."""

    class Meta:
        model = EmailConfig
        fields = "__all__"
        widgets = {"smtp_password": forms.PasswordInput(render_value=True)}


@admin.register(EmailConfig)
class EmailConfigAdmin(admin.ModelAdmin):
    """Admin view for the singleton SMTP configuration."""

    form = EmailConfigForm
    list_display = ("__str__", "smtp_host", "smtp_port", "use_tls", "default_from_email")

    def has_add_permission(self, request) -> bool:  # type: ignore[override]
        """Only one EmailConfig row is allowed."""
        return not EmailConfig.objects.exists()

    def has_delete_permission(self, request, obj=None) -> bool:  # type: ignore[override]
        """Prevent deletion of the singleton."""
        return False

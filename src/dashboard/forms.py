# dashboard/forms.py
"""Forms for the dashboard application."""

import logging

from django import forms

from core.models import Entity

logger = logging.getLogger(__name__)

# Curated subset of commonly-needed IANA timezone names for the profile dropdown.
# Validation is enforced by ChoiceField — invalid values are rejected at form clean time.
COMMON_TIMEZONES = [
    "UTC",
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "America/Mexico_City",
    "America/Bogota",
    "America/Lima",
    "America/Santiago",
    "America/Sao_Paulo",
    "America/Buenos_Aires",
    "America/Toronto",
    "America/Vancouver",
    "Pacific/Honolulu",
    "Europe/London",
    "Europe/Dublin",
    "Europe/Lisbon",
    "Europe/Paris",
    "Europe/Berlin",
    "Europe/Madrid",
    "Europe/Rome",
    "Europe/Warsaw",
    "Europe/Athens",
    "Europe/Istanbul",
    "Europe/Moscow",
    "Africa/Cairo",
    "Africa/Johannesburg",
    "Africa/Lagos",
    "Africa/Nairobi",
    "Asia/Dubai",
    "Asia/Karachi",
    "Asia/Kolkata",
    "Asia/Dhaka",
    "Asia/Bangkok",
    "Asia/Singapore",
    "Asia/Hong_Kong",
    "Asia/Shanghai",
    "Asia/Tokyo",
    "Asia/Seoul",
    "Australia/Perth",
    "Australia/Adelaide",
    "Australia/Sydney",
    "Pacific/Auckland",
]

TIMEZONE_CHOICES = [(tz, tz) for tz in COMMON_TIMEZONES]


class UserProfileForm(forms.Form):
    """Form for updating user profile settings: watchlist, digest preference, and timezone."""

    watched_entities = forms.ModelMultipleChoiceField(
        queryset=Entity.objects.select_related("vertical").order_by("vertical__name", "name"),
        widget=forms.CheckboxSelectMultiple,
        required=False,
        help_text="Select the entities you want to track.",
    )
    digest_enabled = forms.BooleanField(
        required=False,
        help_text="Receive a weekly digest email summarising changes.",
    )
    timezone = forms.ChoiceField(
        choices=TIMEZONE_CHOICES,
        required=True,
        help_text="Display times in this timezone.",
    )

# core/middleware.py
"""Request-scoped timezone activation middleware."""

import logging
import zoneinfo

from django.utils import timezone

logger = logging.getLogger(__name__)


class TimezoneMiddleware:
    """
    Activate the requesting user's preferred timezone for the duration of
    the request so that all Django date/time rendering respects it.

    Falls back to settings.TIME_ZONE for anonymous users and for any
    stored value that is not a valid IANA timezone name.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tz_name = self._resolve_timezone(request)
        if tz_name:
            timezone.activate(zoneinfo.ZoneInfo(tz_name))
        else:
            timezone.deactivate()  # falls back to settings.TIME_ZONE
        return self.get_response(request)

    @staticmethod
    def _resolve_timezone(request) -> str | None:
        """Return a validated IANA tz name, or None to use the server default."""
        if not request.user.is_authenticated:
            return None

        try:
            raw = request.user.profile.timezone
        except Exception:
            # Profile may not exist yet (race between signal and first request)
            return None

        if not raw:
            return None

        try:
            zoneinfo.ZoneInfo(raw)  # raises ZoneInfoNotFoundError if invalid
            return raw
        except (zoneinfo.ZoneInfoNotFoundError, KeyError):
            logger.warning(
                "Invalid timezone %r stored for user=%s; falling back to server default",
                raw,
                request.user.pk,
            )
            return None

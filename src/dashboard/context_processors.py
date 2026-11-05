"""Template context processors for the dashboard app."""

from django.conf import settings
from django.http import HttpRequest


def primary_org(request: HttpRequest) -> dict[str, str]:
    """Expose the primary organisation's name to every template as ``primary_org``."""
    return {"primary_org": settings.PRIMARY_ORG_NAME}

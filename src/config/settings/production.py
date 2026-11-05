# config/settings/production.py
"""
Production settings.

Extends base with DEBUG off and security hardening enabled.
All secrets must be explicitly set via environment variables — no defaults.
"""

from .base import *  # noqa: F401, F403

# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------
DEBUG = False

ALLOWED_HOSTS: list[str] = env.list("ALLOWED_HOSTS")  # noqa: F405  # must be set in env

# ---------------------------------------------------------------------------
# Security hardening
# ---------------------------------------------------------------------------
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31_536_000  # 1 year
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_CONTENT_TYPE_NOSNIFF = True
CSRF_TRUSTED_ORIGINS: list[str] = env.list("CSRF_TRUSTED_ORIGINS", default=[])  # noqa: F405

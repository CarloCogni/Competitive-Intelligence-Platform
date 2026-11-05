# config/settings/dev.py
"""
Development settings.

Extends base with DEBUG on, a fallback insecure secret key, and
no production security hardening. Never use this in production.
"""

from .base import *  # noqa: F401, F403

# ---------------------------------------------------------------------------
# Core — relaxed for local development
# ---------------------------------------------------------------------------
DEBUG = True

SECRET_KEY = env(  # noqa: F405
    "SECRET_KEY",
    default="django-insecure-rpx97v6f9_f%=u*a-o2@m-c$dua7&-vqs-**(2*9xl9k9s#^-x",
)

ALLOWED_HOSTS: list[str] = env.list(  # noqa: F405
    "ALLOWED_HOSTS",
    default=["localhost", "127.0.0.1"],
)

# ---------------------------------------------------------------------------
# Email — print to console, no SMTP needed locally
# ---------------------------------------------------------------------------
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

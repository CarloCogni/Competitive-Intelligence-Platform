# config/settings/base.py
"""
Base Django settings shared across all environments.

Environment-specific overrides live in dev.py and production.py.
Secrets and runtime flags are read from a .env file via django-environ.
"""

from pathlib import Path

import environ

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# base.py lives at src/config/settings/base.py → three parents up = src/
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
env = environ.Env(
    DEBUG=(bool, False),
    RUN_SCHEDULER=(bool, True),
    GITHUB_TOKEN=(str, ""),
    NEWS_API_KEY=(str, ""),
    TWITTER_BEARER_TOKEN=(str, ""),
    OLLAMA_BASE_URL=(str, "http://localhost:11434"),
    OLLAMA_MODEL=(str, "gemma4:e4b"),
    GROQ_API_KEY=(str, ""),
    GROQ_MODEL=(str, "llama-3.1-8b-instant"),
    EMAIL_HOST=(str, ""),
    EMAIL_PORT=(int, 587),
    EMAIL_HOST_USER=(str, ""),
    EMAIL_HOST_PASSWORD=(str, ""),
    EMAIL_USE_TLS=(bool, True),
    DEFAULT_FROM_EMAIL=(str, "noreply@compintel.local"),
    SITE_URL=(str, "http://localhost:8000"),
    NEWS_ENRICHMENT_OLLAMA_ONLY=(bool, True),
    PRIMARY_ORG_NAME=(str, "Blockstream"),
    PRIMARY_ORG_DESCRIPTION=(str, "a Bitcoin infrastructure company"),
)
environ.Env.read_env(BASE_DIR.parent / ".env")

# ---------------------------------------------------------------------------
# Secrets & runtime flags
# ---------------------------------------------------------------------------
SECRET_KEY: str = env("SECRET_KEY", default="change-me-in-production")
GITHUB_TOKEN: str = env("GITHUB_TOKEN")
NEWS_API_KEY: str = env("NEWS_API_KEY")
TWITTER_BEARER_TOKEN: str = env("TWITTER_BEARER_TOKEN")
RUN_SCHEDULER: bool = env("RUN_SCHEDULER")

# ---------------------------------------------------------------------------
# Primary organisation — whose products are flagged is_primary in entities.json.
# Used in UI labels ("<org> vs Competitors") and in LLM prompts.
# ---------------------------------------------------------------------------
PRIMARY_ORG_NAME: str = env("PRIMARY_ORG_NAME")
PRIMARY_ORG_DESCRIPTION: str = env("PRIMARY_ORG_DESCRIPTION")

# ---------------------------------------------------------------------------
# Ollama — local LLM service for AI-powered features
# ---------------------------------------------------------------------------
OLLAMA_BASE_URL: str = env("OLLAMA_BASE_URL")
OLLAMA_MODEL: str = env("OLLAMA_MODEL")

# ---------------------------------------------------------------------------
# Groq — cloud LLM fallback when Ollama is unavailable (VPS deployment)
# ---------------------------------------------------------------------------
GROQ_API_KEY: str = env("GROQ_API_KEY")
GROQ_MODEL: str = env("GROQ_MODEL")

# ---------------------------------------------------------------------------
# LLM context window budgets (approximate token limits for content payloads)
# ---------------------------------------------------------------------------
OLLAMA_CONTEXT_BUDGET: int = 80_000  # ~128k window, 60% safety margin
GROQ_CONTEXT_BUDGET: int = 25_000  # conservative default for Groq models

# ---------------------------------------------------------------------------
# News enrichment — LLM sentiment/classification for news articles
# ---------------------------------------------------------------------------
NEWS_ENRICHMENT_OLLAMA_ONLY: bool = env("NEWS_ENRICHMENT_OLLAMA_ONLY")

# ---------------------------------------------------------------------------
# Anomaly detection — Isolation Forest model storage
# ---------------------------------------------------------------------------
ANOMALY_MODEL_DIR: Path = BASE_DIR / "data" / "anomaly_models"

# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    # Django built-ins
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "django_htmx",
    "django_apscheduler",
    # Project apps
    "core",
    "metrics",
    "fetchers",
    "scheduler",
    "dashboard",
    "briefings",
]

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "core.middleware.TimezoneMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
]

ROOT_URLCONF = "config.urls"

# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "dashboard.context_processors.primary_org",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# ---------------------------------------------------------------------------
# Database — SQLite, zero setup
# ---------------------------------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": env("DB_PATH", default=str(BASE_DIR / "db.sqlite3")),
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Password validation
# ---------------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ---------------------------------------------------------------------------
# Internationalisation
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "America/Mexico_City"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/overview/"
LOGOUT_REDIRECT_URL = "/"

# ---------------------------------------------------------------------------
# Email — switches to SMTP when EMAIL_HOST is set, console backend otherwise
# ---------------------------------------------------------------------------
EMAIL_HOST: str = env("EMAIL_HOST")
EMAIL_PORT: int = env("EMAIL_PORT")
EMAIL_HOST_USER: str = env("EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD: str = env("EMAIL_HOST_PASSWORD")
EMAIL_USE_TLS: bool = env("EMAIL_USE_TLS")
DEFAULT_FROM_EMAIL: str = env("DEFAULT_FROM_EMAIL")
SITE_URL: str = env("SITE_URL")

EMAIL_BACKEND = (
    "django.core.mail.backends.smtp.EmailBackend"
    if EMAIL_HOST
    else "django.core.mail.backends.console.EmailBackend"
)

# ---------------------------------------------------------------------------
# Logging — structured, one logger per module
# ---------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "[%(levelname)s] %(name)s: %(message)s"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "simple",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
}

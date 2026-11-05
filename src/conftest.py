# conftest.py
"""Root pytest configuration: Django settings and shared fixtures."""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

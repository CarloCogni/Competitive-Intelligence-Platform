# config/urls.py
"""Root URL configuration."""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    # Built-in auth views: login, logout, password change, etc.
    path("accounts/", include("django.contrib.auth.urls")),
    path("", include("dashboard.urls", namespace="dashboard")),
]

# dashboard/urls.py
"""URL configuration for the dashboard application."""

from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    # Public landing pages (no auth required)
    path("", views.landing_v4, name="landing"),
    path("landing/v1/", views.landing_v1, name="landing_v1"),
    path("landing/v2/", views.landing_v2, name="landing_v2"),
    path("landing/v3/", views.landing_v3, name="landing_v3"),
    # Full-page views
    path("dashboard/", views.main_dashboard, name="main_dashboard"),
    path("landscape/", views.landscape_view, name="landscape"),
    path("vertical/<slug:slug>/", views.vertical_detail, name="vertical_detail"),
    path("entity/<slug:slug>/", views.entity_detail, name="entity_detail"),
    path("compare/", views.head_to_head, name="head_to_head"),
    path("feed/", views.feed, name="feed"),
    path("news/", views.news_intelligence, name="news_intelligence"),
    path("news/key-takeaways/", views.news_key_takeaways, name="news_key_takeaways"),
    path("alerts/", views.alerts, name="alerts"),
    path("alerts/mark-all-seen/", views.mark_all_alerts_seen, name="mark_all_alerts_seen"),
    path("alerts/clear-seen/", views.clear_seen_alerts, name="clear_seen_alerts"),
    path(
        "alerts/entity/<slug:slug>/mark-all-seen/",
        views.mark_entity_alerts_seen,
        name="mark_entity_alerts_seen",
    ),
    path("alerts/<int:pk>/mark-seen/", views.mark_alert_seen, name="mark_alert_seen"),
    path("alerts/<int:pk>/delete/", views.delete_alert, name="delete_alert"),
    path("alerts/<int:pk>/undismiss/", views.undismiss_alert, name="undismiss_alert"),
    # Competitor candidates
    path("candidates/", views.candidate_list, name="candidate_list"),
    path(
        "candidates/<int:pk>/promote/",
        views.promote_candidate,
        name="promote_candidate",
    ),
    path(
        "candidates/<int:pk>/dismiss/",
        views.dismiss_candidate,
        name="dismiss_candidate",
    ),
    path(
        "candidates/dismiss-all/",
        views.dismiss_all_candidates,
        name="dismiss_all_candidates",
    ),
    # HTMX partial endpoints
    path("partials/entity-table/", views.partial_entity_table, name="partial_entity_table"),
    path("partials/alerts/", views.partial_alerts, name="partial_alerts"),
    path(
        "partials/dismissed-alerts/",
        views.partial_dismissed_alerts,
        name="partial_dismissed_alerts",
    ),
    path("partials/comparison/", views.partial_comparison, name="partial_comparison"),
    path(
        "partials/sparkline/<slug:entity_slug>/<str:metric_key>/",
        views.partial_sparkline,
        name="partial_sparkline",
    ),
    # Dashboard JSON endpoints
    path(
        "partials/exec-vertical-bars/",
        views.partial_exec_vertical_bars,
        name="partial_exec_vertical_bars",
    ),
    path("partials/exec-radar/", views.partial_exec_radar, name="partial_exec_radar"),
    path("partials/exec-severity/", views.partial_exec_severity, name="partial_exec_severity"),
    path(
        "partials/exec-news-volume/",
        views.partial_exec_news_volume,
        name="partial_exec_news_volume",
    ),
    path("partials/exec-trend/", views.partial_exec_trend, name="partial_exec_trend"),
    path("partials/exec-movers/", views.partial_exec_movers, name="partial_exec_movers"),
    # Chart JSON endpoints
    path("partials/ranking-chart/", views.partial_ranking_chart, name="partial_ranking_chart"),
    path(
        "partials/news-histogram/<slug:entity_slug>/",
        views.partial_news_histogram,
        name="partial_news_histogram",
    ),
    path("partials/dual-trend/", views.partial_dual_trend, name="partial_dual_trend"),
    # Metric deep-dive chart (Plotly full-page)
    path(
        "entity/<slug:entity_slug>/metric/<str:metric_key>/",
        views.metric_chart_detail,
        name="metric_chart_detail",
    ),
    # Peers JSON endpoint for competitor overlay
    path(
        "entity/<slug:entity_slug>/metric/<str:metric_key>/peers/",
        views.metric_chart_peers,
        name="metric_chart_peers",
    ),
    # Watchlist toggle (HTMX POST, returns watch_button partial)
    path("entity/<slug:slug>/watch/", views.toggle_watch, name="toggle_watch"),
    # Global scope toggle (POST, redirects back to referer)
    path("toggle-scope/", views.toggle_scope, name="toggle_scope"),
    # Save a head-to-head comparison (HTMX POST, returns saved_comparisons partial)
    path("comparisons/save/", views.save_comparison, name="save_comparison"),
    # AI Observatory
    path("ai/", views.ai_observatory, name="ai_observatory"),
    path("ai/anomaly-data/", views.ai_anomaly_data, name="ai_anomaly_data"),
    path("ai/ga-data/", views.ai_ga_data, name="ai_ga_data"),
    path("ai/alert-volume-data/", views.ai_alert_volume_data, name="ai_alert_volume_data"),
    path("ai/threshold-history/", views.ai_threshold_history, name="ai_threshold_history"),
    path(
        "ai/threshold-evolution/",
        views.ai_threshold_evolution_page,
        name="ai_threshold_evolution_page",
    ),
    path(
        "ai/threshold-evolution-data/",
        views.ai_threshold_evolution_data,
        name="ai_threshold_evolution",
    ),
    # Momentum leaderboard
    path("momentum/", views.momentum_leaderboard, name="momentum"),
    path("momentum/chart-data/", views.momentum_chart_data, name="momentum_chart_data"),
    # Scheduler dashboard
    path("scheduler/", views.scheduler_status, name="scheduler_status"),
    path("scheduler/chart-data/", views.scheduler_chart_data, name="scheduler_chart_data"),
    # User profile settings
    path("profile/settings/", views.profile_settings, name="profile_settings"),
    # Weekly AI Briefing
    path("briefing/", views.briefing, name="briefing"),
    path("briefing/generate/", views.briefing_generate, name="briefing_generate"),
    path("briefing/settings/", views.briefing_settings, name="briefing_settings"),
    path("briefing/models/", views.briefing_models_json, name="briefing_models_json"),
    path(
        "partials/briefing-status/<int:pk>/",
        views.partial_briefing_status,
        name="partial_briefing_status",
    ),
]

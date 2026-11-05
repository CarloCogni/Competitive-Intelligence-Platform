# dashboard/views.py
"""
Dashboard views.

Views are thin: they handle HTTP and template rendering only.
All non-trivial query logic lives in helper functions below the views.
"""

import json
import logging
from collections import defaultdict
from datetime import date, timedelta
from urllib.parse import urlparse

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db.models import Count, Exists, Max, OuterRef, Q, Subquery
from django.db.models.functions import TruncDate
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.models import Entity, UserProfile, Vertical
from dashboard.forms import UserProfileForm
from dashboard.models import SavedComparison
from dashboard.services import save_profile_settings
from metrics.models import Alert, AlertSeen, Metric, MomentumScore, NewsItem

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public landing pages (no auth required)
# ---------------------------------------------------------------------------

_NEXT_URL = "/dashboard/"


def landing(request: HttpRequest) -> HttpResponse:
    """Public landing page: typewriter headline, stat blocks and a canvas node network.

    Redirects authenticated users directly to the dashboard overview.
    """
    if request.user.is_authenticated:
        return redirect("dashboard:main_dashboard")
    return render(request, "landing/landing.html", {"next": _NEXT_URL})


@login_required
def profile_settings(request: HttpRequest) -> HttpResponse:
    """
    GET: render profile settings form pre-populated with current state.
    POST: validate, save, redirect (PRG pattern).
    """
    profile, _ = UserProfile.objects.get_or_create(user=request.user)

    if request.method == "POST":
        form = UserProfileForm(request.POST)
        if form.is_valid():
            save_profile_settings(
                profile,
                form.cleaned_data["watched_entities"],
                form.cleaned_data["digest_enabled"],
                form.cleaned_data["timezone"],
            )
            messages.success(request, "Settings saved.")
            return redirect("dashboard:profile_settings")
    else:
        form = UserProfileForm(
            initial={
                "watched_entities": profile.watched_entities.all(),
                "digest_enabled": profile.digest_enabled,
                "timezone": profile.timezone,
            }
        )

    entities = Entity.objects.select_related("vertical").order_by("vertical__name", "name")
    watched_ids = set(profile.watched_entities.values_list("pk", flat=True))

    return render(
        request,
        "dashboard/profile_settings.html",
        {
            "form": form,
            "entities": entities,
            "watched_ids": watched_ids,
            "unseen_alert_count": _unseen_count(request.user),
        },
    )


# Metric keys shown in the overview table and comparison panel
OVERVIEW_METRIC_KEYS = [
    "github_stars",
    "github_forks",
    "github_open_issues",
    "github_public_repos",
    "github_latest_release_days_ago",
    "github_commits_30d",
    "github_commits_90d",
    "github_contributors",
    "appstore_rating",
    "appstore_rating_count",
    "appstore_last_update_days_ago",
    "playstore_rating",
    "playstore_rating_count",
    "playstore_last_update_days_ago",
    "twitter_mention_count",
    "twitter_total_engagement",
    "twitter_avg_engagement",
    "reddit_mention_count",
    "news_article_count",
    "changelog_releases_total",
    "changelog_latest_release_days_ago",
]

# Column groups for the overview table header
METRIC_GROUPS: list[dict[str, str | list[str]]] = [
    {
        "label": "GitHub",
        "keys": [
            "github_stars",
            "github_forks",
            "github_open_issues",
            "github_public_repos",
            "github_latest_release_days_ago",
            "github_commits_30d",
            "github_commits_90d",
            "github_contributors",
        ],
    },
    {
        "label": "App Store",
        "keys": [
            "appstore_rating",
            "appstore_rating_count",
            "appstore_last_update_days_ago",
        ],
    },
    {
        "label": "Play Store",
        "keys": [
            "playstore_rating",
            "playstore_rating_count",
            "playstore_last_update_days_ago",
        ],
    },
    {
        "label": "Social",
        "keys": [
            "twitter_mention_count",
            "twitter_total_engagement",
            "twitter_avg_engagement",
            "reddit_mention_count",
        ],
    },
    {
        "label": "News / RSS",
        "keys": [
            "news_article_count",
            "changelog_releases_total",
            "changelog_latest_release_days_ago",
        ],
    },
]

# Human-readable labels for metric keys
METRIC_LABELS: dict[str, str] = {
    "github_stars": "Stars",
    "github_forks": "Forks",
    "github_open_issues": "Open Issues",
    "github_public_repos": "Public Repos",
    "github_latest_release_days_ago": "Days Since Release",
    "github_commits_30d": "Commits (30d)",
    "github_commits_90d": "Commits (90d)",
    "github_contributors": "Contributors",
    "appstore_rating": "App Rating",
    "appstore_rating_count": "Rating Count",
    "appstore_last_update_days_ago": "Days Since App Update",
    "news_article_count": "News Articles",
    "twitter_mention_count": "Twitter Mentions",
    "twitter_total_engagement": "Twitter Engagement",
    "twitter_avg_engagement": "Avg Tweet Engagement",
    "reddit_mention_count": "Reddit Mentions",
    "playstore_rating": "Play Store Rating",
    "playstore_rating_count": "Play Store Ratings",
    "playstore_last_update_days_ago": "Days Since Play Store Update",
    "changelog_releases_total": "Releases (RSS)",
    "changelog_latest_release_days_ago": "Days Since Release (RSS)",
}

# Metrics where a lower value is better (used for radar normalization and sort order)
INVERTED_METRICS: set[str] = {
    "github_latest_release_days_ago",
    "appstore_last_update_days_ago",
    "playstore_last_update_days_ago",
    "changelog_latest_release_days_ago",
}


# ---------------------------------------------------------------------------
# Full-page views
# ---------------------------------------------------------------------------


@login_required
def entity_detail(request: HttpRequest, slug: str) -> HttpResponse:
    """Drill-down page for a single entity: metrics, sparklines, news, alerts."""
    entity = get_object_or_404(Entity, slug=slug)

    latest_metrics = _latest_metrics_for_entity(entity)
    recent_news = NewsItem.objects.filter(entities=entity).order_by("-published_at")[:20]
    recent_alerts = Alert.objects.filter(entity=entity).order_by("-created_at")[:3]

    # Keys that have enough history to render a sparkline
    sparkline_keys = list(
        Metric.objects.filter(entity=entity).values_list("key", flat=True).distinct()
    )

    is_watched = request.user.profile.watched_entities.filter(slug=slug).exists()

    # Latest momentum score for this entity
    momentum = MomentumScore.objects.filter(entity=entity).order_by("-computed_at").first()

    context = {
        "entity": entity,
        "latest_metrics": latest_metrics,
        "metric_labels": METRIC_LABELS,
        "sparkline_keys": sparkline_keys,
        "recent_news": recent_news,
        "recent_alerts": recent_alerts,
        "unseen_alert_count": _unseen_count(request.user),
        "is_watched": is_watched,
        "momentum": momentum,
    }
    return render(request, "dashboard/entity_detail.html", context)


@login_required
def metric_chart_detail(request: HttpRequest, entity_slug: str, metric_key: str) -> HttpResponse:
    """
    Full-page Plotly deep-dive for a single metric of one entity.

    Provides all historical readings, a 7-day rolling average, related alert
    annotations, and summary stats. The template renders an interactive Plotly
    chart with native range selectors and a range slider.
    """
    entity = get_object_or_404(Entity, slug=entity_slug)

    readings = list(
        Metric.objects.filter(entity=entity, key=metric_key)
        .order_by("captured_at")
        .values_list("captured_at", "value")
    )

    if not readings:
        labels: list[str] = []
        values: list[float] = []
        rolling_avg: list[float | None] = []
    else:
        labels = [ts.strftime("%Y-%m-%d") for ts, _ in readings]
        values = [float(v) if v is not None else None for _, v in readings]

        # 7-reading rolling average (one reading ≈ 24 h)
        window = 7
        rolling_avg = []
        for i, v in enumerate(values):
            window_vals = [x for x in values[max(0, i - window + 1) : i + 1] if x is not None]
            rolling_avg.append(
                round(sum(window_vals) / len(window_vals), 4) if window_vals else None
            )

    # Stats
    non_null = [v for v in values if v is not None]
    current_val = non_null[-1] if non_null else None
    stats = {
        "current": current_val,
        "min": min(non_null) if non_null else None,
        "max": max(non_null) if non_null else None,
        "avg": round(sum(non_null) / len(non_null), 2) if non_null else None,
        "count": len(non_null),
        # Trend: compare latest vs 7 readings back
        "trend": None,
        "trend_pct": None,
    }
    if len(non_null) >= 2:
        base = non_null[max(0, len(non_null) - 8)]
        if base:
            raw_trend = ((non_null[-1] - base) / abs(base)) * 100
            stats["trend"] = "up" if raw_trend >= 0 else "down"
            stats["trend_pct"] = round(abs(raw_trend), 1)

    # Related alerts (filter by metric_key stored in metadata JSON)
    # Alert.metadata is a JSONField; we do a relaxed icontains search on the key
    related_alerts = (
        Alert.objects.filter(entity=entity)
        .filter(metadata__metric_key=metric_key)
        .order_by("-created_at")[:30]
    )

    metric_label = METRIC_LABELS.get(metric_key, metric_key.replace("_", " ").title())
    is_inverted = metric_key in INVERTED_METRICS

    context = {
        "entity": entity,
        "metric_key": metric_key,
        "metric_label": metric_label,
        "is_inverted": is_inverted,
        "is_primary": entity.is_primary,
        "chart_data": {"labels": labels, "values": values, "rolling": rolling_avg},
        "stats": stats,
        "related_alerts": related_alerts,
        "unseen_alert_count": _unseen_count(request.user),
    }
    return render(request, "dashboard/metric_chart.html", context)


@login_required
def metric_chart_peers(request: HttpRequest, entity_slug: str, metric_key: str) -> JsonResponse:
    """
    JSON endpoint returning all entities' full reading history for a given metric key.

    Used by the competitor overlay panel on the metric deep-dive page.
    Response: { peers: [{ entity_id, entity_name, slug, is_primary, is_current,
                          labels, values }, ...] }
    """
    entity_ids = (
        Metric.objects.filter(key=metric_key).values_list("entity_id", flat=True).distinct()
    )
    entities = Entity.objects.filter(pk__in=entity_ids).order_by("name")

    peers = []
    for ent in entities:
        readings = list(
            Metric.objects.filter(entity=ent, key=metric_key)
            .order_by("captured_at")
            .values_list("captured_at", "value")
        )
        peers.append(
            {
                "entity_id": ent.id,
                "entity_name": ent.name,
                "slug": ent.slug,
                "is_primary": ent.is_primary,
                "is_current": ent.slug == entity_slug,
                "labels": [ts.strftime("%Y-%m-%d") for ts, _ in readings],
                "values": [float(v) if v is not None else None for _, v in readings],
            }
        )

    return JsonResponse({"peers": peers, "metric_key": metric_key})


@login_required
def vertical_detail(request: HttpRequest, slug: str) -> HttpResponse:
    """
    Intermediate drill-down: lists all entities in a vertical with key metrics.
    Breadcrumb: Overview > Vertical Name
    """
    vertical = get_object_or_404(Vertical, slug=slug)
    sort_key = request.GET.get("sort", "name")

    entities = _get_entities_with_metrics(sort_key, vertical_slug=slug)

    watched_slugs = _get_watched_slugs(request.user)
    if watched_slugs is not None:
        entities = [e for e in entities if e.slug in watched_slugs]

    context = {
        "vertical": vertical,
        "entities": entities,
        "metric_keys": OVERVIEW_METRIC_KEYS,
        "metric_groups": METRIC_GROUPS,
        "metric_labels": METRIC_LABELS,
        "current_sort": sort_key,
        "unseen_alert_count": _unseen_count(request.user),
    }
    return render(request, "dashboard/vertical_detail.html", context)


@login_required
def head_to_head(request: HttpRequest) -> HttpResponse:
    """
    Side-by-side comparison of two entities.
    Entity selection via Alpine.js dropdowns; comparison panel loaded via HTMX.
    Dropdown list respects the user's global scope setting.
    """
    entities = Entity.objects.select_related("vertical").order_by("name")
    watched_slugs = _get_watched_slugs(request.user)
    if watched_slugs is not None:
        entities = entities.filter(slug__in=watched_slugs)
    saved_comparisons = SavedComparison.objects.filter(user=request.user).select_related(
        "entity_a", "entity_b"
    )
    context = {
        "entities": entities,
        "metric_labels": METRIC_LABELS,
        "unseen_alert_count": _unseen_count(request.user),
        "saved_comparisons": saved_comparisons,
    }
    return render(request, "dashboard/head_to_head.html", context)


@login_required
def feed(request: HttpRequest) -> HttpResponse:
    """
    Three-column unified feed: News | Releases | Alerts.
    Filterable server-side by ?entity=<slug>; each column also has a
    client-side name filter (Alpine.js, no page reload).
    Respects the user's scope setting (watchlist vs all).
    """
    entity_slug = request.GET.get("entity", "")
    watched_slugs = _get_watched_slugs(request.user)

    entities = Entity.objects.order_by("name")
    if watched_slugs is not None:
        entities = entities.filter(slug__in=watched_slugs)

    # --- News column ---
    news_qs = NewsItem.objects.prefetch_related("entities").order_by("-published_at")
    if entity_slug:
        news_qs = news_qs.filter(entities__slug=entity_slug)
    elif watched_slugs is not None:
        news_qs = news_qs.filter(entities__slug__in=watched_slugs).distinct()
    news_items = list(news_qs[:60])

    # --- Releases column: NEW_RELEASE and NEW_REPO alerts ---
    release_types = [Alert.AlertType.NEW_RELEASE, Alert.AlertType.NEW_REPO]
    release_qs = (
        Alert.objects.select_related("entity")
        .filter(alert_type__in=release_types)
        .order_by("-created_at")
    )
    if entity_slug:
        release_qs = release_qs.filter(entity__slug=entity_slug)
    elif watched_slugs is not None:
        release_qs = release_qs.filter(entity__slug__in=watched_slugs)
    release_items = list(release_qs[:60])

    # --- Alerts column: metric spikes, news surges, competitor candidates ---
    spike_types = [
        Alert.AlertType.METRIC_SPIKE,
        Alert.AlertType.NEWS_SURGE,
        Alert.AlertType.COMPETITOR_NEW,
    ]
    alert_qs = (
        Alert.objects.select_related("entity")
        .filter(alert_type__in=spike_types)
        .order_by("-created_at")
    )
    if entity_slug:
        alert_qs = alert_qs.filter(entity__slug=entity_slug)
    elif watched_slugs is not None:
        alert_qs = alert_qs.filter(entity__slug__in=watched_slugs)
    alert_items = list(alert_qs[:60])

    context = {
        "news_items": news_items,
        "release_items": release_items,
        "alert_items": alert_items,
        "entities": entities,
        "current_entity": entity_slug,
        "unseen_alert_count": _unseen_count(request.user),
    }
    return render(request, "dashboard/feed.html", context)


@login_required
def alerts(request: HttpRequest) -> HttpResponse:
    """Alerts page. ?dismissed=1 shows dismissed view; ?entity=<slug> pre-filters by entity."""
    show_dismissed = request.GET.get("dismissed") == "1"
    initial_entity = request.GET.get("entity", "all")
    watched_slugs = _get_watched_slugs(request.user)
    seen_ids = _get_seen_alert_ids(request.user)

    alert_types = [
        ("all", "All", "bg-gray-700"),
        ("metric_spike", "Metric Spike", "bg-yellow-700"),
        ("new_release", "New Release", "bg-green-700"),
        ("new_repo", "New Repo", "bg-teal-700"),
        ("news_surge", "News Surge", "bg-blue-700"),
        ("competitor_new", "Competitor", "bg-cyan-700"),
        ("compound_spike", "Compound Spike", "bg-purple-700"),
    ]
    severity_choices = [
        ("all", "All", "bg-gray-700"),
        ("HIGH", "High", "bg-red-700"),
        ("MEDIUM", "Medium", "bg-orange-700"),
        ("LOW", "Low", "bg-blue-700"),
    ]

    if show_dismissed:
        # Dismissed view: show alerts the user has already seen, with retriage controls.
        alert_qs = Alert.objects.select_related("entity", "entity__vertical").filter(
            pk__in=seen_ids
        )
        if watched_slugs is not None:
            alert_qs = alert_qs.filter(entity__slug__in=watched_slugs)
        all_alerts = list(
            _annotate_alerts_for_user(alert_qs.order_by("-created_at")[:100], request.user)
        )
    else:
        # Active view: unseen alerts only, HTMX-pollable.
        alert_qs = Alert.objects.select_related("entity", "entity__vertical").exclude(
            pk__in=seen_ids
        )
        if watched_slugs is not None:
            alert_qs = alert_qs.filter(entity__slug__in=watched_slugs)
        all_alerts = list(alert_qs.order_by("-created_at")[:50])
        if request.htmx:
            return render(request, "partials/alert_card.html", {"alerts": all_alerts})

    entity_map = {a.entity.slug: a.entity.name for a in all_alerts}
    alert_entities = sorted(entity_map.items(), key=lambda t: t[1])

    vertical_map = {
        a.entity.vertical.slug: a.entity.vertical.name
        for a in all_alerts
        if a.entity.vertical_id is not None
    }
    alert_verticals = sorted(vertical_map.items(), key=lambda t: t[1])

    unseen_count = Alert.objects.exclude(pk__in=seen_ids).count()
    total_count = Alert.objects.count()
    high_count = sum(1 for a in all_alerts if a.metadata.get("severity") == "HIGH")
    medium_count = sum(1 for a in all_alerts if a.metadata.get("severity") == "MEDIUM")

    context = {
        "alerts": all_alerts,
        "alert_entities": alert_entities,
        "alert_verticals": alert_verticals,
        "unseen_alert_count": unseen_count,
        "total_count": total_count,
        "high_count": high_count,
        "medium_count": medium_count,
        "alert_types": alert_types,
        "severity_choices": severity_choices,
        "show_dismissed": show_dismissed,
        "initial_entity": initial_entity,
    }
    return render(request, "dashboard/alerts.html", context)


@login_required
@require_POST
def mark_all_alerts_seen(request: HttpRequest) -> HttpResponse:
    """Mark every unseen alert in the active watchlist as Relevant or Noise (GA training)."""
    # Parse GA feedback signal from POST
    useful_raw = request.POST.get("useful")
    useful: bool | None = (
        True if useful_raw == "true" else (False if useful_raw == "false" else None)
    )
    unseen_alerts = Alert.objects.exclude(pk__in=_get_seen_alert_ids(request.user))
    # Respect watchlist scope: only mark alerts visible under the current toggle
    watched_slugs = _get_watched_slugs(request.user)
    if watched_slugs is not None:
        unseen_alerts = unseen_alerts.filter(entity__slug__in=watched_slugs)
    AlertSeen.objects.bulk_create(
        [AlertSeen(user=request.user, alert=a, useful=useful) for a in unseen_alerts],
        ignore_conflicts=True,
    )
    unseen_count = _unseen_count(request.user)
    return render(request, "partials/mark_all_seen_response.html", {"unseen_count": unseen_count})


@login_required
@require_POST
def mark_entity_alerts_seen(request: HttpRequest, slug: str) -> HttpResponse:
    """Mark all unseen alerts for one entity as Relevant or Noise for GA training."""
    entity = get_object_or_404(Entity, slug=slug)
    useful_raw = request.POST.get("useful")
    useful: bool | None = (
        True if useful_raw == "true" else (False if useful_raw == "false" else None)
    )
    unseen = Alert.objects.filter(entity=entity).exclude(pk__in=_get_seen_alert_ids(request.user))
    AlertSeen.objects.bulk_create(
        [AlertSeen(user=request.user, alert=a, useful=useful) for a in unseen],
        ignore_conflicts=True,
    )
    unseen_count = _unseen_count(request.user)
    return render(request, "partials/entity_alerts_dismissed.html", {"unseen_count": unseen_count})


@login_required
@require_POST
def mark_alert_seen(request: HttpRequest, pk: int) -> HttpResponse:
    """Mark a single alert as seen for the current user with optional GA feedback."""
    alert = get_object_or_404(Alert, pk=pk)
    # Parse explicit feedback signal for GA training
    useful_raw = request.POST.get("useful")
    useful: bool | None = None
    if useful_raw == "true":
        useful = True
    elif useful_raw == "false":
        useful = False
    AlertSeen.objects.update_or_create(
        user=request.user,
        alert=alert,
        defaults={"useful": useful},
    )
    unseen_count = _unseen_count(request.user)
    # Retriage: user is changing label on an already-dismissed alert — re-render card in place.
    if request.POST.get("retriage") == "true":
        qs = Alert.objects.filter(pk=pk).select_related("entity")
        alert_annotated = _annotate_alerts_for_user(qs, request.user).first()
        return render(
            request,
            "partials/alert_card_single.html",
            {"alert": alert_annotated, "unseen_count": unseen_count},
        )
    return render(request, "partials/mark_seen_response.html", {"unseen_count": unseen_count})


@login_required
@require_POST
def delete_alert(request: HttpRequest, pk: int) -> HttpResponse:
    """Permanently delete a single alert (affects all users)."""
    Alert.objects.filter(pk=pk).delete()
    unseen_count = _unseen_count(request.user)
    return render(request, "partials/mark_seen_response.html", {"unseen_count": unseen_count})


@login_required
@require_POST
def undismiss_alert(request: HttpRequest, pk: int) -> HttpResponse:
    """Remove the user's AlertSeen record, returning the alert to unseen state.

    Returns an empty swap so the card disappears from the dismissed section.
    The alert will reappear in the main feed on the next HTMX poll.
    """
    alert = get_object_or_404(Alert, pk=pk)
    AlertSeen.objects.filter(user=request.user, alert=alert).delete()
    unseen_count = _unseen_count(request.user)
    return render(request, "partials/mark_seen_response.html", {"unseen_count": unseen_count})


@login_required
@require_POST
def clear_seen_alerts(request: HttpRequest) -> HttpResponse:
    """Permanently delete all alerts the current user has already seen."""
    Alert.objects.filter(pk__in=_get_seen_alert_ids(request.user)).delete()
    response = HttpResponse()
    response["HX-Redirect"] = "/alerts/"
    return response


# ---------------------------------------------------------------------------
# Watchlist endpoint
# ---------------------------------------------------------------------------


@login_required
@require_POST
def toggle_watch(request: HttpRequest, slug: str) -> HttpResponse:
    """Toggle watch/unwatch for an entity. Returns the updated button partial."""
    entity = get_object_or_404(Entity, slug=slug)
    profile = request.user.profile
    if profile.watched_entities.filter(pk=entity.pk).exists():
        profile.watched_entities.remove(entity)
        is_watched = False
    else:
        profile.watched_entities.add(entity)
        is_watched = True
    return render(
        request,
        "partials/watch_button.html",
        {"entity": entity, "is_watched": is_watched},
    )


# ---------------------------------------------------------------------------
# Scope toggle endpoint
# ---------------------------------------------------------------------------


@login_required
@require_POST
def toggle_scope(request: HttpRequest) -> HttpResponse:
    """Toggle the user's global entity scope between watchlist and all entities."""
    profile = request.user.profile
    profile.show_all_entities = not profile.show_all_entities
    profile.save(update_fields=["show_all_entities"])
    # Validate referer is same-origin to prevent open-redirect attacks.
    referer = request.META.get("HTTP_REFERER", "/")
    parsed = urlparse(referer)
    if parsed.netloc and parsed.netloc != request.get_host():
        referer = "/"
    return redirect(referer)


# ---------------------------------------------------------------------------
# Saved comparisons endpoint
# ---------------------------------------------------------------------------


@login_required
@require_POST
def save_comparison(request: HttpRequest) -> HttpResponse:
    """Save or overwrite a named head-to-head comparison for the current user."""
    name = request.POST.get("name", "").strip()
    slug_a = request.POST.get("entity_a", "")
    slug_b = request.POST.get("entity_b", "")

    if not name or not slug_a or not slug_b:
        return HttpResponse(status=400)

    entity_a = get_object_or_404(Entity, slug=slug_a)
    entity_b = get_object_or_404(Entity, slug=slug_b)

    SavedComparison.objects.update_or_create(
        user=request.user,
        name=name,
        defaults={"entity_a": entity_a, "entity_b": entity_b},
    )

    saved_comparisons = SavedComparison.objects.filter(user=request.user).select_related(
        "entity_a", "entity_b"
    )
    return render(
        request,
        "partials/saved_comparisons.html",
        {"saved_comparisons": saved_comparisons},
    )


# ---------------------------------------------------------------------------
# HTMX partial endpoints
# ---------------------------------------------------------------------------


@login_required
def partial_entity_table(request: HttpRequest) -> HttpResponse:
    """Returns entity table rows for sort swaps. Mirrors scope logic from overview()."""
    sort_key = request.GET.get("sort", "name")

    entities = _get_entities_with_metrics(sort_key, vertical_slug="")

    watched_slugs = _get_watched_slugs(request.user)
    if watched_slugs is not None:
        entities = [e for e in entities if e.slug in watched_slugs]

    context = {
        "entities": entities,
        "metric_keys": OVERVIEW_METRIC_KEYS,
        "metric_groups": METRIC_GROUPS,
        "metric_labels": METRIC_LABELS,
        "current_sort": sort_key,
    }
    return render(request, "partials/entity_table.html", context)


@login_required
def partial_alerts(request: HttpRequest) -> HttpResponse:
    """Returns alert cards for HTMX polling refresh (per-user unseen only, scope-aware)."""
    seen_ids = _get_seen_alert_ids(request.user)
    alert_qs = Alert.objects.select_related("entity", "entity__vertical").exclude(pk__in=seen_ids)
    watched_slugs = _get_watched_slugs(request.user)
    if watched_slugs is not None:
        alert_qs = alert_qs.filter(entity__slug__in=watched_slugs)
    all_alerts = alert_qs.order_by("-created_at")[:50]
    return render(request, "partials/alert_card.html", {"alerts": all_alerts})


@login_required
def partial_dismissed_alerts(request: HttpRequest) -> HttpResponse:
    """HTMX partial: dismissed alert cards for the retriage section, annotated with seen/useful."""
    seen_ids = _get_seen_alert_ids(request.user)
    if not seen_ids:
        return render(request, "partials/dismissed_alerts_list.html", {"alerts": []})
    qs = Alert.objects.filter(pk__in=seen_ids).select_related("entity").order_by("-created_at")
    watched_slugs = _get_watched_slugs(request.user)
    if watched_slugs is not None:
        qs = qs.filter(entity__slug__in=watched_slugs)
    alerts = _annotate_alerts_for_user(qs[:100], request.user)
    return render(request, "partials/dismissed_alerts_list.html", {"alerts": alerts})


@login_required
def partial_comparison(request: HttpRequest) -> HttpResponse:
    """Returns the side-by-side comparison panel for two entities."""
    slug_a = request.GET.get("a", "")
    slug_b = request.GET.get("b", "")

    entity_a = Entity.objects.filter(slug=slug_a).first() if slug_a else None
    entity_b = Entity.objects.filter(slug=slug_b).first() if slug_b else None

    metrics_a = _latest_metrics_for_entity(entity_a) if entity_a else {}
    metrics_b = _latest_metrics_for_entity(entity_b) if entity_b else {}

    all_keys = sorted(set(metrics_a) | set(metrics_b))

    radar_data = _build_radar_data(metrics_a, metrics_b, all_keys)

    wins_a, wins_b = 0, 0
    for key in all_keys:
        va, vb = metrics_a.get(key), metrics_b.get(key)
        if va is None or vb is None or va == vb:
            continue
        a_wins = (va < vb) if key in INVERTED_METRICS else (va > vb)
        if a_wins:
            wins_a += 1
        else:
            wins_b += 1

    context = {
        "entity_a": entity_a,
        "entity_b": entity_b,
        "metrics_a": metrics_a,
        "metrics_b": metrics_b,
        "all_keys": all_keys,
        "metric_labels": METRIC_LABELS,
        "metric_labels_json": json.dumps(METRIC_LABELS),
        "radar_labels": radar_data["labels"],
        "radar_values_a": radar_data["values_a"],
        "radar_values_b": radar_data["values_b"],
        "wins_a": wins_a,
        "wins_b": wins_b,
    }
    return render(request, "partials/comparison_panel.html", context)


@login_required
def partial_sparkline(request: HttpRequest, entity_slug: str, metric_key: str) -> JsonResponse:
    """
    Returns JSON data for a Chart.js sparkline.
    Response: { labels: [...], values: [...] }
    """
    entity = get_object_or_404(Entity, slug=entity_slug)

    readings = (
        Metric.objects.filter(entity=entity, key=metric_key)
        .order_by("captured_at")
        .values_list("captured_at", "value")
    )

    labels = [ts.strftime("%Y-%m-%d") for ts, _ in readings]
    values = [v for _, v in readings]

    return JsonResponse({"labels": labels, "values": values})


@login_required
def partial_ranking_chart(request: HttpRequest) -> JsonResponse:
    """
    Returns JSON for a horizontal bar chart ranking entities by a metric key.
    Respects the user's global scope setting (watchlist vs all).
    GET ?key=<metric_key>
    """
    key = request.GET.get("key", "github_stars")

    latest_sq = (
        Metric.objects.filter(entity=OuterRef("pk"), key=key)
        .order_by("-captured_at")
        .values("value")[:1]
    )
    order = "latest_val" if key in INVERTED_METRICS else "-latest_val"
    entity_qs = Entity.objects.annotate(latest_val=Subquery(latest_sq)).filter(
        latest_val__isnull=False
    )
    watched_slugs = _get_watched_slugs(request.user)
    if watched_slugs is not None:
        entity_qs = entity_qs.filter(slug__in=watched_slugs)
    entities = entity_qs.order_by(order)

    if not entities:
        return JsonResponse(
            {
                "labels": [],
                "values": [],
                "colors": [],
                "slugs": [],
                "label": METRIC_LABELS.get(key, key),
            }
        )

    return JsonResponse(
        {
            "labels": [e.name for e in entities],
            "values": [float(e.latest_val) for e in entities],
            "colors": ["#f7931a" if e.is_primary else "#6b7280" for e in entities],
            "slugs": [e.slug for e in entities],
            "label": METRIC_LABELS.get(key, key),
        }
    )


@login_required
def partial_news_histogram(request: HttpRequest, entity_slug: str) -> JsonResponse:
    """
    Returns daily article counts for the last 30 days for a given entity.
    Response: { labels: ["YYYY-MM-DD", ...], values: [int, ...] }
    """
    entity = get_object_or_404(Entity, slug=entity_slug)

    today = date.today()
    date_range = [today - timedelta(days=i) for i in range(29, -1, -1)]

    counts_qs = (
        NewsItem.objects.filter(
            entities=entity,
            published_at__date__gte=date_range[0],
        )
        .annotate(pub_date=TruncDate("published_at"))
        .values("pub_date")
        .annotate(count=Count("id"))
        .order_by("pub_date")
    )
    counts_map = {row["pub_date"].strftime("%Y-%m-%d"): row["count"] for row in counts_qs}

    labels = [d.strftime("%Y-%m-%d") for d in date_range]
    values = [counts_map.get(lbl, 0) for lbl in labels]

    return JsonResponse({"labels": labels, "values": values})


@login_required
def partial_dual_trend(request: HttpRequest) -> JsonResponse:
    """
    Returns aligned time-series for two entities on a single metric key.
    GET ?a=<slug>&b=<slug>&key=<metric_key>
    """
    slug_a = request.GET.get("a", "")
    slug_b = request.GET.get("b", "")
    key = request.GET.get("key", "github_stars")

    entity_a = Entity.objects.filter(slug=slug_a).first() if slug_a else None
    entity_b = Entity.objects.filter(slug=slug_b).first() if slug_b else None

    def _series(entity: Entity | None) -> dict[str, float]:
        """Return {date_str: value} for one entity, keeping last reading per day."""
        if not entity:
            return {}
        result: dict[str, float] = {}
        for ts, val in (
            Metric.objects.filter(entity=entity, key=key)
            .order_by("captured_at")
            .values_list("captured_at", "value")
        ):
            result[ts.strftime("%Y-%m-%d")] = val
        return result

    sa, sb = _series(entity_a), _series(entity_b)
    all_dates = sorted(set(sa) | set(sb))

    datasets = []
    for entity, series in [(entity_a, sa), (entity_b, sb)]:
        if entity:
            datasets.append(
                {
                    "label": entity.name,
                    "values": [series.get(d) for d in all_dates],
                    "is_primary": entity.is_primary,
                }
            )

    return JsonResponse(
        {
            "labels": all_dates,
            "datasets": datasets,
            "metric_label": METRIC_LABELS.get(key, key),
        }
    )


# ---------------------------------------------------------------------------
# Scheduler views
# ---------------------------------------------------------------------------


@login_required
def scheduler_status(request: HttpRequest) -> HttpResponse:
    """
    Scheduler health dashboard: recent BatchRun history per job.
    Supports HTMX partial refresh (hx-get every 30s).
    """
    context = _build_scheduler_context(request.user)
    if request.htmx:
        return render(request, "partials/scheduler_runs.html", context)
    return render(request, "dashboard/scheduler.html", context)


@login_required
def scheduler_chart_data(request: HttpRequest) -> JsonResponse:
    """Returns hourly run counts for the last 48 hours, bucketed per job."""
    from django.db.models.functions import TruncHour

    from scheduler.jobs import SOURCE_TO_JOB
    from scheduler.models import BatchRun

    now = timezone.now().replace(minute=0, second=0, microsecond=0)
    cutoff = now - timedelta(hours=47)

    hours = [now - timedelta(hours=i) for i in range(47, -1, -1)]
    labels = [h.strftime("%Y-%m-%d %H:%M") for h in hours]
    hour_index = {h.strftime("%Y-%m-%d %H:%M"): i for i, h in enumerate(hours)}

    runs_qs = (
        BatchRun.objects.filter(triggered_at__gte=cutoff)
        .annotate(hour=TruncHour("triggered_at"))
        .values("job_id", "status", "hour")
        .annotate(count=Count("id"))
    )

    datasets = []
    for _source, (job_id, display_name, _fn) in SOURCE_TO_JOB.items():
        success = [0] * 48
        partial = [0] * 48
        failed = [0] * 48

        for row in runs_qs:
            if row["job_id"] != job_id:
                continue
            label = row["hour"].strftime("%Y-%m-%d %H:%M")
            idx = hour_index.get(label)
            if idx is None:
                continue
            if row["status"] == "success":
                success[idx] += row["count"]
            elif row["status"] == "partial":
                partial[idx] += row["count"]
            elif row["status"] == "failed":
                failed[idx] += row["count"]

        datasets.append(
            {
                "job_id": job_id,
                "label": display_name,
                "success": success,
                "partial": partial,
                "failed": failed,
            }
        )

    return JsonResponse({"labels": labels, "datasets": datasets})


def _build_scheduler_context(user) -> dict:
    """Build context dict for the scheduler status page."""

    from scheduler.jobs import SOURCE_TO_JOB
    from scheduler.models import BatchRun, FetcherConfig

    cutoff = timezone.now() - timedelta(hours=48)
    recent_runs = list(BatchRun.objects.filter(triggered_at__gte=cutoff).order_by("-triggered_at"))

    job_summaries = []
    for _source, (job_id, display_name, _fn) in SOURCE_TO_JOB.items():
        job_runs = [r for r in recent_runs if r.job_id == job_id]

        next_run = None
        try:
            from django_apscheduler.models import DjangoJob

            dj = DjangoJob.objects.filter(id=job_id).first()
            next_run = dj.next_run_time if dj else None
        except Exception:
            pass

        job_summaries.append(
            {
                "job_id": job_id,
                "display_name": display_name,
                "runs": job_runs[:20],
                "last_run": job_runs[0] if job_runs else None,
                "next_run_time": next_run,
                "run_count_48h": len(job_runs),
                "failed_count_48h": sum(1 for r in job_runs if r.status in ("failed", "partial")),
            }
        )

    configs = {cfg.source: cfg for cfg in FetcherConfig.objects.all()}

    return {
        "job_summaries": job_summaries,
        "configs": configs,
        "unseen_alert_count": _unseen_count(user),
    }


# ---------------------------------------------------------------------------
# Competitor candidate views
# ---------------------------------------------------------------------------


@login_required
def candidate_list(request: HttpRequest) -> HttpResponse:
    """List competitor candidates with promote/dismiss actions."""
    from metrics.models import CandidateEntity

    candidates = CandidateEntity.objects.all().order_by("-mention_count")

    context = {
        "candidates": candidates,
        "pending_count": candidates.filter(status=CandidateEntity.Status.PENDING).count(),
        "unseen_alert_count": _unseen_count(request.user),
    }
    return render(request, "dashboard/candidates.html", context)


@login_required
@require_POST
def promote_candidate(request: HttpRequest, pk: int) -> HttpResponse:
    """
    Promote a CandidateEntity to a full tracked Entity.

    Auto-populates:
      - news_keywords from the candidate name (news fetcher picks it up immediately)
      - reddit_keywords from the candidate name
      - github_org via a best-effort GitHub org/user search
    """
    from django.utils.text import slugify

    from metrics.models import CandidateEntity

    candidate = get_object_or_404(CandidateEntity, pk=pk)

    # Auto-populate API identifiers from the candidate name
    name = candidate.name
    news_kw = [name]
    reddit_kw = [name]
    github_org = _guess_github_org(name)

    entity, created = Entity.objects.get_or_create(
        slug=slugify(name),
        defaults={
            "name": name,
            "is_primary": False,
            "news_keywords": news_kw,
            "reddit_keywords": reddit_kw,
            "github_org": github_org,
        },
    )

    candidate.status = CandidateEntity.Status.PROMOTED
    candidate.promoted_entity = entity
    candidate.save(update_fields=["status", "promoted_entity"])

    if request.htmx:
        # When promoted from an alert card, return a compact badge instead of the full candidate row
        if request.POST.get("from_alert") == "true":
            return render(request, "partials/alert_promoted_badge.html", {"candidate": candidate})
        return render(request, "partials/candidate_row.html", {"candidate": candidate})
    return redirect("dashboard:candidate_list")


@login_required
@require_POST
def dismiss_candidate(request: HttpRequest, pk: int) -> HttpResponse:
    """Dismiss a CandidateEntity (mark as not interesting)."""
    from metrics.models import CandidateEntity

    candidate = get_object_or_404(CandidateEntity, pk=pk)
    candidate.status = CandidateEntity.Status.DISMISSED
    candidate.save(update_fields=["status"])

    if request.htmx:
        return render(request, "partials/candidate_row.html", {"candidate": candidate})
    return redirect("dashboard:candidate_list")


@login_required
@require_POST
def dismiss_all_candidates(request: HttpRequest) -> HttpResponse:
    """Dismiss all pending CandidateEntity rows at once."""
    from metrics.models import CandidateEntity

    updated = CandidateEntity.objects.filter(status=CandidateEntity.Status.PENDING).update(
        status=CandidateEntity.Status.DISMISSED
    )

    logger.info("Dismissed %d pending candidate(s).", updated)

    if request.htmx:
        return HttpResponse(
            '<div class="text-center text-gray-600 py-12">'
            '<p class="text-lg mb-2">All candidates dismissed</p>'
            '<p class="text-sm">New candidates will appear after the next news fetch cycle.</p>'
            "</div>"
        )
    return redirect("dashboard:candidate_list")


# ---------------------------------------------------------------------------
# Candidate promotion helpers
# ---------------------------------------------------------------------------


def _guess_github_org(name: str) -> str:
    """
    Best-effort GitHub org/user lookup by name.

    Queries the GitHub search API for users/orgs matching the candidate name.
    Returns the login of the first result, or empty string if nothing found.
    Non-fatal: network errors return empty string.
    """
    import requests
    from django.conf import settings

    query = name.replace(" ", "+")
    headers = {"Accept": "application/vnd.github+json"}

    # Use token if available to avoid rate limits
    token = getattr(settings, "GITHUB_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        resp = requests.get(
            f"https://api.github.com/search/users?q={query}+type:org",
            headers=headers,
            timeout=10,
        )
        if resp.status_code == 200:
            items = resp.json().get("items", [])
            if items:
                login = items[0].get("login", "")
                logger.info("GitHub org guess for '%s': %s", name, login)
                return login
    except requests.RequestException as exc:
        logger.debug("GitHub org search failed for '%s': %s", name, exc)

    return ""


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


def _get_watched_slugs(user: User) -> set[str] | None:
    """Return the set of watched entity slugs, or None if the user sees all entities."""
    if user.profile.show_all_entities:
        return None
    return set(user.profile.watched_entities.values_list("slug", flat=True))


def _get_seen_alert_ids(user: User) -> set[int]:
    """Return the set of alert PKs the user has already dismissed."""
    return set(AlertSeen.objects.filter(user=user).values_list("alert_id", flat=True))


def _unseen_count(user: User) -> int:
    """Return the number of alerts the given user has not yet dismissed."""
    return Alert.objects.exclude(pk__in=_get_seen_alert_ids(user)).count()


def _annotate_alerts_for_user(qs, user: User):
    """Annotate an Alert queryset with per-user seen/useful_value from AlertSeen."""
    user_seen = AlertSeen.objects.filter(user=user, alert=OuterRef("pk"))
    return qs.annotate(
        is_dismissed=Exists(user_seen),
        useful_value=Subquery(user_seen.values("useful")[:1]),
    )


def _latest_metrics_for_entity(entity: Entity) -> dict[str, float | None]:
    """
    Return a dict of {metric_key: latest_value} for a single entity.

    Uses a single query: groups by key and takes the latest captured_at
    value for each via a subquery filter.
    """

    # Get the latest captured_at per key in one query
    latest_dates = (
        Metric.objects.filter(entity=entity).values("key").annotate(max_date=Max("captured_at"))
    )
    if not latest_dates:
        return {}

    # Build filter conditions for all (key, max_date) pairs

    q_filter = Q()
    for row in latest_dates:
        q_filter |= Q(key=row["key"], captured_at=row["max_date"])

    latest_metrics = Metric.objects.filter(entity=entity).filter(q_filter)
    return {m.key: m.value for m in latest_metrics}


def _get_entities_with_metrics(sort_key: str, vertical_slug: str) -> list[Entity]:
    """
    Return a list of entity dicts enriched with their latest metric values.
    Supports sorting by name or any metric key; filtering by vertical slug.
    """
    qs = Entity.objects.select_related("vertical")
    if vertical_slug:
        qs = qs.filter(vertical__slug=vertical_slug)

    entities = list(qs)

    for entity in entities:
        entity.latest_metrics = _latest_metrics_for_entity(entity)  # type: ignore[attr-defined]

    if sort_key == "name":
        entities.sort(key=lambda e: e.name.lower())
    else:
        entities.sort(
            key=lambda e: (
                e.latest_metrics.get(sort_key) is None,  # type: ignore[attr-defined]
                -(e.latest_metrics.get(sort_key) or 0),  # type: ignore[attr-defined]
            )
        )

    return entities


def _build_radar_data(
    metrics_a: dict[str, float | None],
    metrics_b: dict[str, float | None],
    all_keys: list[str],
) -> dict[str, list]:
    """
    Normalize two entities' metrics to 0-100 for radar chart display.

    For each key:
    - score = (value / max(a, b)) * 100
    - For INVERTED_METRICS: score = (1 - value / max(a, b)) * 100  (lower is better)
    - Keys where both values are None are excluded.
    - When both values are 0, both scores are set to 50 (tie at midpoint).
    """
    labels: list[str] = []
    values_a: list[float] = []
    values_b: list[float] = []

    for key in all_keys:
        va = metrics_a.get(key)
        vb = metrics_b.get(key)
        if va is None and vb is None:
            continue

        fa = float(va) if va is not None else 0.0
        fb = float(vb) if vb is not None else 0.0
        max_val = max(fa, fb)

        if max_val == 0:
            sa, sb = 50.0, 50.0
        elif key in INVERTED_METRICS:
            sa = round((1 - fa / max_val) * 100, 1)
            sb = round((1 - fb / max_val) * 100, 1)
        else:
            sa = round(fa / max_val * 100, 1)
            sb = round(fb / max_val * 100, 1)

        labels.append(METRIC_LABELS.get(key, key))
        values_a.append(sa)
        values_b.append(sb)

    return {"labels": labels, "values_a": values_a, "values_b": values_b}


# ---------------------------------------------------------------------------
# Executive Dashboard
# ---------------------------------------------------------------------------

# Metrics used for the executive radar chart — broad coverage across sources
EXEC_RADAR_KEYS: list[str] = [
    "github_stars",
    "github_forks",
    "github_commits_30d",
    "github_contributors",
    "appstore_rating",
    "twitter_total_engagement",
    "reddit_mention_count",
    "news_article_count",
]

# Metrics shown in the 3×2 competitive trend wall
EXEC_TREND_KEYS: list[str] = [
    "github_stars",
    "github_forks",
    "github_commits_30d",
    "appstore_rating",
    "twitter_total_engagement",
    "reddit_mention_count",
]


@login_required
def main_dashboard(request: HttpRequest) -> HttpResponse:
    """
    Main dashboard — chart-dense competitive intelligence cockpit.

    Renders KPI cards, ranking bar chart, vertical stacked bar, capability
    radar, alert severity donut, news volume area chart, 6 competitive trend
    mini-charts, top-movers table, and the entity overview table.
    """
    context = _build_dashboard_context(request)
    return render(request, "dashboard/dashboard.html", context)


def _build_dashboard_context(request: HttpRequest) -> dict:
    """Build all template context for the main dashboard in one pass."""
    user = request.user

    now = timezone.now()
    week_ago = now - timedelta(days=7)

    # Entity counts
    entities = list(Entity.objects.select_related("vertical").all())
    watched_slugs = _get_watched_slugs(user)
    if watched_slugs is not None:
        entities = [e for e in entities if e.slug in watched_slugs]

    total_count = len(entities)
    primary_count = sum(1 for e in entities if e.is_primary)
    competitor_count = total_count - primary_count

    # Unseen alerts
    unseen_alert_count = _unseen_count(user)

    # News count last 7 days
    news_7d = NewsItem.objects.filter(published_at__gte=week_ago).count()

    # Releases last 7 days
    releases_7d = Alert.objects.filter(
        alert_type__in=[Alert.AlertType.NEW_RELEASE, Alert.AlertType.NEW_REPO],
        created_at__gte=week_ago,
    ).count()

    # Alert severity breakdown (unseen)
    seen_ids = _get_seen_alert_ids(user)
    unseen_alerts = Alert.objects.exclude(pk__in=seen_ids)
    severity_high = sum(
        1
        for m in unseen_alerts.values_list("metadata", flat=True)
        if (m or {}).get("severity") == "HIGH"
    )
    severity_medium = sum(
        1
        for m in unseen_alerts.values_list("metadata", flat=True)
        if (m or {}).get("severity") == "MEDIUM"
    )
    severity_low = max(0, unseen_alert_count - severity_high - severity_medium)

    # Trend keys and labels for the JS side (raw Python objects; json_script encodes them)
    trend_keys = EXEC_TREND_KEYS
    trend_labels = {k: METRIC_LABELS.get(k, k) for k in EXEC_TREND_KEYS}

    # Overview table data (merged into executive dashboard)
    sort_key = "name"
    all_entities = _get_entities_with_metrics(sort_key, vertical_slug="")
    if watched_slugs is not None:
        all_entities = [e for e in all_entities if e.slug in watched_slugs]
    verticals = Vertical.objects.all()

    return {
        "total_count": total_count,
        "primary_count": primary_count,
        "competitor_count": competitor_count,
        "unseen_alert_count": unseen_alert_count,
        "news_7d": news_7d,
        "releases_7d": releases_7d,
        "severity_high": severity_high,
        "severity_medium": severity_medium,
        "severity_low": severity_low,
        "trend_keys": trend_keys,
        "trend_labels": trend_labels,
        "metric_labels": METRIC_LABELS,
        # Overview table data
        "entities": all_entities,
        "verticals": verticals,
        "current_sort": sort_key,
        "metric_keys": OVERVIEW_METRIC_KEYS,
        "metric_groups": METRIC_GROUPS,
    }


@login_required
def partial_exec_vertical_bars(request: HttpRequest) -> JsonResponse:
    """
    Returns stacked bar data: Blockstream vs competitor totals per vertical.

    GET ?key=<metric_key> (default: github_stars)
    Response: { verticals: [...], primary: [...], competitor: [...], label: str }
    """
    key = request.GET.get("key", "github_stars")

    latest_sq = (
        Metric.objects.filter(entity=OuterRef("pk"), key=key)
        .order_by("-captured_at")
        .values("value")[:1]
    )
    entities = Entity.objects.annotate(latest_val=Subquery(latest_sq)).select_related("vertical")

    watched_slugs = _get_watched_slugs(request.user)
    if watched_slugs is not None:
        entities = entities.filter(slug__in=watched_slugs)

    # Aggregate by vertical
    vertical_data: dict[str, dict[str, float]] = {}
    for e in entities:
        vname = e.vertical.name if e.vertical else "Other"
        if vname not in vertical_data:
            vertical_data[vname] = {"primary": 0.0, "competitor": 0.0}
        val = float(e.latest_val) if e.latest_val is not None else 0.0
        bucket = "primary" if e.is_primary else "competitor"
        vertical_data[vname][bucket] += val

    # Sort by total descending
    sorted_verticals = sorted(
        vertical_data.items(), key=lambda x: x[1]["primary"] + x[1]["competitor"], reverse=True
    )

    return JsonResponse(
        {
            "verticals": [v[0] for v in sorted_verticals],
            "primary": [v[1]["primary"] for v in sorted_verticals],
            "competitor": [v[1]["competitor"] for v in sorted_verticals],
            "label": METRIC_LABELS.get(key, key),
        }
    )


@login_required
def partial_exec_radar(request: HttpRequest) -> JsonResponse:
    """
    Returns radar chart data: avg Blockstream vs avg competitors across key metrics.

    Response: { labels: [...], primary: [...], competitor: [...] }
    Values are normalized to 0-100.
    """
    entities = list(Entity.objects.all())
    watched_slugs = _get_watched_slugs(request.user)
    if watched_slugs is not None:
        entities = [e for e in entities if e.slug in watched_slugs]

    # Gather latest metric per entity for radar keys
    primary_sums: dict[str, list[float]] = {k: [] for k in EXEC_RADAR_KEYS}
    competitor_sums: dict[str, list[float]] = {k: [] for k in EXEC_RADAR_KEYS}

    for entity in entities:
        latest = _latest_metrics_for_entity(entity)
        target = primary_sums if entity.is_primary else competitor_sums
        for key in EXEC_RADAR_KEYS:
            val = latest.get(key)
            if val is not None:
                target[key].append(float(val))

    # Compute averages and normalize to 0-100
    labels: list[str] = []
    primary_vals: list[float] = []
    competitor_vals: list[float] = []

    for key in EXEC_RADAR_KEYS:
        p_vals = primary_sums[key]
        c_vals = competitor_sums[key]
        if not p_vals and not c_vals:
            continue

        p_avg = sum(p_vals) / len(p_vals) if p_vals else 0.0
        c_avg = sum(c_vals) / len(c_vals) if c_vals else 0.0
        max_val = max(p_avg, c_avg)

        if max_val == 0:
            p_norm, c_norm = 50.0, 50.0
        elif key in INVERTED_METRICS:
            p_norm = round((1 - p_avg / max_val) * 100, 1)
            c_norm = round((1 - c_avg / max_val) * 100, 1)
        else:
            p_norm = round(p_avg / max_val * 100, 1)
            c_norm = round(c_avg / max_val * 100, 1)

        labels.append(METRIC_LABELS.get(key, key))
        primary_vals.append(p_norm)
        competitor_vals.append(c_norm)

    return JsonResponse({"labels": labels, "primary": primary_vals, "competitor": competitor_vals})


@login_required
def partial_exec_severity(request: HttpRequest) -> JsonResponse:
    """
    Returns severity distribution of the user's unseen alerts, scoped to the active watchlist.

    Matches the KPI card counts so numbers are consistent across the dashboard.
    Response: { high: int, medium: int, low: int }
    """
    seen_ids = _get_seen_alert_ids(request.user)
    unseen_alerts = Alert.objects.exclude(pk__in=seen_ids)
    # Respect watchlist scope so the chart matches what the user sees on the alerts page
    watched_slugs = _get_watched_slugs(request.user)
    if watched_slugs is not None:
        unseen_alerts = unseen_alerts.filter(entity__slug__in=watched_slugs)

    counts = {"high": 0, "medium": 0, "low": 0}
    for meta in unseen_alerts.values_list("metadata", flat=True):
        sev = (meta or {}).get("severity", "LOW").lower()
        if sev in counts:
            counts[sev] += 1
        else:
            counts["low"] += 1

    return JsonResponse(counts)


@login_required
def partial_exec_news_volume(request: HttpRequest) -> JsonResponse:
    """
    Returns daily news article counts for the last 30 days, scoped to the active watchlist.

    Response: { labels: ["YYYY-MM-DD", ...], values: [int, ...] }
    """
    today = date.today()
    date_range = [today - timedelta(days=i) for i in range(29, -1, -1)]

    # Filter to watched entities when not in "show all" mode
    news_qs = NewsItem.objects.filter(published_at__date__gte=date_range[0])
    watched_slugs = _get_watched_slugs(request.user)
    if watched_slugs is not None:
        news_qs = news_qs.filter(entities__slug__in=watched_slugs).distinct()

    counts_qs = (
        news_qs.annotate(pub_date=TruncDate("published_at"))
        .values("pub_date")
        .annotate(count=Count("id"))
        .order_by("pub_date")
    )
    counts_map = {row["pub_date"].strftime("%Y-%m-%d"): row["count"] for row in counts_qs}

    labels = [d.strftime("%Y-%m-%d") for d in date_range]
    values = [counts_map.get(lbl, 0) for lbl in labels]

    return JsonResponse({"labels": labels, "values": values})


@login_required
def partial_exec_trend(request: HttpRequest) -> JsonResponse:
    """
    Returns multi-entity time series for one metric: all primary entities +
    top 3 competitors (by latest value).

    GET ?key=<metric_key>
    Response: { labels: [...], datasets: [{ label, values, is_primary, color }, ...] }
    """
    key = request.GET.get("key", "github_stars")

    # Find primary entities and top 3 competitors for this metric
    latest_sq = (
        Metric.objects.filter(entity=OuterRef("pk"), key=key)
        .order_by("-captured_at")
        .values("value")[:1]
    )

    all_entities = Entity.objects.annotate(latest_val=Subquery(latest_sq)).filter(
        latest_val__isnull=False
    )

    watched_slugs = _get_watched_slugs(request.user)
    if watched_slugs is not None:
        all_entities = all_entities.filter(slug__in=watched_slugs)

    primaries = list(all_entities.filter(is_primary=True))
    competitors = list(all_entities.filter(is_primary=False).order_by("-latest_val")[:3])
    selected = primaries + competitors

    if not selected:
        return JsonResponse({"labels": [], "datasets": []})

    # Competitor color palette (gray tones for differentiation)
    comp_colors = ["#6b7280", "#9ca3af", "#d1d5db"]

    # Build time series for each selected entity
    all_dates_set: set[str] = set()
    entity_series: list[dict] = []

    for i, entity in enumerate(selected):
        readings = (
            Metric.objects.filter(entity=entity, key=key)
            .order_by("captured_at")
            .values_list("captured_at", "value")
        )
        series: dict[str, float] = {}
        for ts, val in readings:
            d = ts.strftime("%Y-%m-%d")
            series[d] = val
            all_dates_set.add(d)

        color = (
            "#f7931a"
            if entity.is_primary
            else comp_colors[i - len(primaries)]
            if (i - len(primaries)) < len(comp_colors)
            else "#6b7280"
        )
        entity_series.append(
            {
                "label": entity.name,
                "series": series,
                "is_primary": entity.is_primary,
                "color": color,
            }
        )

    all_dates = sorted(all_dates_set)

    datasets = []
    for es in entity_series:
        datasets.append(
            {
                "label": es["label"],
                "values": [es["series"].get(d) for d in all_dates],
                "is_primary": es["is_primary"],
                "color": es["color"],
            }
        )

    return JsonResponse({"labels": all_dates, "datasets": datasets})


@login_required
def partial_exec_movers(request: HttpRequest) -> JsonResponse:
    """
    Returns the top 5 entities with the biggest metric changes in the last 7 days.

    Response: { movers: [{ name, slug, metric, current, delta_pct, is_primary }, ...] }
    """

    now = timezone.now()
    week_ago = now - timedelta(days=7)

    # Get entities
    entities = list(Entity.objects.all())
    watched_slugs = _get_watched_slugs(request.user)
    if watched_slugs is not None:
        entities = [e for e in entities if e.slug in watched_slugs]

    # For each entity, find the metric with the biggest absolute change
    movers: list[dict] = []
    check_keys = [
        "github_stars",
        "github_forks",
        "twitter_total_engagement",
        "reddit_mention_count",
    ]

    two_weeks_ago = now - timedelta(days=14)

    for entity in entities:
        best_delta = 0.0
        best_info: dict | None = None

        for key in check_keys:
            latest = (
                Metric.objects.filter(entity=entity, key=key)
                .order_by("-captured_at")
                .values_list("value", flat=True)
                .first()
            )
            baseline = (
                Metric.objects.filter(entity=entity, key=key, captured_at__lte=week_ago)
                .order_by("-captured_at")
                .values_list("value", flat=True)
                .first()
            )
            if latest is None or baseline is None or baseline == 0:
                continue
            delta_pct = ((latest - baseline) / abs(baseline)) * 100
            if abs(delta_pct) > abs(best_delta):
                best_delta = delta_pct
                best_info = {
                    "name": entity.name,
                    "slug": entity.slug,
                    "metric": METRIC_LABELS.get(key, key),
                    "current": latest,
                    "delta_pct": round(delta_pct, 1),
                    "is_primary": entity.is_primary,
                }

        # News volume: compare cumulative NewsItem counts week-over-week
        current_news = entity.news_items.filter(
            fetched_at__gt=week_ago, fetched_at__lte=now
        ).count()
        previous_news = entity.news_items.filter(
            fetched_at__gt=two_weeks_ago, fetched_at__lte=week_ago
        ).count()
        if previous_news > 0:
            news_delta = ((current_news - previous_news) / previous_news) * 100
            if abs(news_delta) > abs(best_delta):
                best_delta = news_delta
                best_info = {
                    "name": entity.name,
                    "slug": entity.slug,
                    "metric": METRIC_LABELS.get("news_article_count", "News Articles"),
                    "current": current_news,
                    "delta_pct": round(news_delta, 1),
                    "is_primary": entity.is_primary,
                }

        if best_info:
            movers.append(best_info)

    # Sort by absolute delta descending, take top 5
    movers.sort(key=lambda m: abs(m["delta_pct"]), reverse=True)
    return JsonResponse({"movers": movers[:5]})


# ---------------------------------------------------------------------------
# Network Graph
# ---------------------------------------------------------------------------


@login_required
def landscape_view(request: HttpRequest) -> HttpResponse:
    """
    Force-directed network graph showing entities as nodes clustered by vertical.

    Nodes are coloured by vertical, sized by github_stars, and connected to
    other entities in the same vertical ('competes with' edges). Blockstream
    products get an orange border ring.

    Scope note: this view intentionally ignores the watchlist toggle and always
    shows all entities. The landscape is a market map — filtering nodes would
    produce an incomplete and misleading graph (missing edges, orphaned clusters).
    The watchlist controls monitoring focus, not market knowledge.
    """
    # Vertical colour palette — 7 distinct colours
    vertical_colors = [
        "#3b82f6",  # blue
        "#10b981",  # emerald
        "#f59e0b",  # amber
        "#ef4444",  # red
        "#8b5cf6",  # violet
        "#06b6d4",  # cyan
        "#ec4899",  # pink
    ]

    verticals = list(Vertical.objects.all())
    color_map = {v.slug: vertical_colors[i % len(vertical_colors)] for i, v in enumerate(verticals)}

    # Get latest github_stars for node sizing
    latest_stars_sq = (
        Metric.objects.filter(entity=OuterRef("pk"), key="github_stars")
        .order_by("-captured_at")
        .values("value")[:1]
    )
    entities = list(
        Entity.objects.annotate(stars=Subquery(latest_stars_sq)).select_related("vertical").all()
    )

    # Build nodes
    max_stars = max((e.stars or 0 for e in entities), default=1) or 1
    nodes = []
    for e in entities:
        stars = float(e.stars) if e.stars else 0
        # Node size: 20-60px based on stars (log scale would be better but linear is fine)
        size = 20 + (stars / max_stars) * 40
        nodes.append(
            {
                "data": {
                    "id": e.slug,
                    "label": e.name,
                    "vertical": e.vertical.slug if e.vertical else "other",
                    "vertical_name": e.vertical.name if e.vertical else "Other",
                    "is_primary": e.is_primary,
                    "stars": int(stars),
                    "color": color_map.get(e.vertical.slug, "#6b7280") if e.vertical else "#6b7280",
                    "size": round(size),
                }
            }
        )

    # -----------------------------------------------------------------------
    # Build edges — two data-derived signal types (no vertical-only edges)
    # -----------------------------------------------------------------------
    edges: list[dict] = []
    entity_slug_set = {e.slug for e in entities}

    # --- Edge type A: News co-mentions (amber) ----------------------------
    # Two entities that appear together in the same NewsItem are directly
    # connected via a real-world competitive signal. Edge weight = number of
    # shared articles; width scales from 1–8 px.
    co_mention_counts: dict[tuple[str, str], int] = defaultdict(int)
    # Flat per-entity article list (all articles, ordered DESC by published_at)
    entity_articles: dict[str, list] = defaultdict(list)
    # Per-entity, per-partner article list — powers the filter chips correctly
    # regardless of how many total articles the entity has.
    entity_partner_articles: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))

    news_items = (
        NewsItem.objects.prefetch_related("entities")
        .filter(entities__isnull=False)
        .order_by("-published_at")
    )
    for item in news_items:
        item_slugs = [e.slug for e in item.entities.all() if e.slug in entity_slug_set]

        # Shared article payload — co_slugs carries the other tracked entities
        # so the frontend can render colored badges on each article card.
        base = {
            "title": item.title,
            "url": item.url,
            "source": item.source_name or "",
            "date": item.published_at.strftime("%b %d, %Y") if item.published_at else "",
            # ISO prefix (YYYY-MM-DD) used for the 30-day recency count below
            "date_iso": item.published_at.isoformat()[:10] if item.published_at else "",
        }
        for slug in item_slugs:
            other_slugs = [s for s in item_slugs if s != slug]
            full = {**base, "co_slugs": other_slugs}
            entity_articles[slug].append(full)
            # Also index by each partner so filter lookups are exact (not a
            # scan of the truncated flat list).
            for partner_slug in other_slugs:
                entity_partner_articles[slug][partner_slug].append(full)

        # Count co-mention pairs for edge weights
        for i, slug_a in enumerate(item_slugs):
            for slug_b in item_slugs[i + 1 :]:
                key = (min(slug_a, slug_b), max(slug_a, slug_b))
                co_mention_counts[key] += 1

    for (slug_a, slug_b), count in co_mention_counts.items():
        edges.append(
            {
                "data": {
                    "source": slug_a,
                    "target": slug_b,
                    "type": "news",
                    "weight": count,
                    # Width: 1 px minimum, capped at 8 px
                    "width": min(1 + count * 0.5, 8),
                    "label": f"{count} shared article{'s' if count != 1 else ''}",
                }
            }
        )

    # Build per-entity detail payload for the side panel
    thirty_days_ago = (timezone.now().date() - timedelta(days=30)).isoformat()
    entity_details: dict[str, dict] = {}
    for slug, arts in entity_articles.items():
        recent = sum(1 for a in arts if a["date_iso"] >= thirty_days_ago)
        entity_details[slug] = {
            "total_count": len(arts),
            "recent_count": recent,
            # Flat list: 15 most recent articles (used for the "All" filter view)
            "articles": arts[:15],
            # Per-partner list: up to 8 most recent shared articles per connection.
            # The frontend uses this for filter chips so results are always correct
            # regardless of how many total articles the entity has.
            "by_partner": {
                partner_slug: partner_arts[:8]
                for partner_slug, partner_arts in entity_partner_articles.get(slug, {}).items()
            },
        }

    graph_data = {"nodes": nodes, "edges": edges, "entity_details": entity_details}

    # Legend data
    legend = [{"name": v.name, "color": color_map.get(v.slug, "#6b7280")} for v in verticals]

    return render(
        request,
        "dashboard/landscape.html",
        {
            "graph_data": graph_data,
            "legend": legend,
            "unseen_alert_count": _unseen_count(request.user),
        },
    )


# ---------------------------------------------------------------------------
# Weekly AI Briefing
# ---------------------------------------------------------------------------


@login_required
def briefing(request: HttpRequest) -> HttpResponse:
    """Display the most recent WeeklyBriefing with chart data, or an empty state if none exists."""
    from briefings.models import WeeklyBriefing

    # Support ?pk= for viewing a specific historical briefing
    pk = request.GET.get("pk")
    if pk:
        latest = WeeklyBriefing.objects.filter(pk=pk).first()
    else:
        latest = WeeklyBriefing.objects.first()
    history = WeeklyBriefing.objects.all()[:10]

    # Build chart data for ready briefings
    chart_data = "{}"
    if latest and latest.status == WeeklyBriefing.Status.READY:
        alerts_qs = Alert.objects.filter(
            created_at__gte=latest.period_start,
            created_at__lte=latest.period_end,
        )
        # Scope to watched entities so the briefing reflects the user's monitored set
        watched_slugs = _get_watched_slugs(request.user)
        if watched_slugs is not None:
            alerts_qs = alerts_qs.filter(entity__slug__in=watched_slugs)

        # Alerts by severity derived from metadata["severity"]
        severity_map: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
        for meta in alerts_qs.values_list("metadata", flat=True):
            sev = (meta or {}).get("severity", "low")
            severity_map[sev] = severity_map.get(sev, 0) + 1

        # Top 5 entities by alert count
        top_entities = list(
            alerts_qs.values("entity__name").annotate(count=Count("id")).order_by("-count")[:5]
        )

        chart_data = json.dumps(
            {
                "severity": severity_map,
                "entities": {r["entity__name"]: r["count"] for r in top_entities},
            }
        )

    return render(
        request,
        "dashboard/briefing.html",
        {"briefing": latest, "history": history, "chart_data": chart_data},
    )


@login_required
@require_POST
def briefing_generate(request: HttpRequest) -> HttpResponse:
    """
    Enqueue an on-demand briefing generation.

    Creates a WeeklyBriefing in PENDING status and schedules a one-shot
    APScheduler job (date trigger = now). Returns an HTMX redirect to the
    briefing page so the status partial can start polling.
    """
    from briefings.models import WeeklyBriefing
    from scheduler.jobs import _scheduler, weekly_briefing_job

    # Guard: don't stack multiple in-progress briefings
    in_progress = WeeklyBriefing.objects.filter(
        status__in=[WeeklyBriefing.Status.PENDING, WeeklyBriefing.Status.GENERATING]
    ).first()
    if in_progress:
        if request.htmx:
            return render(
                request,
                "partials/briefing_status.html",
                {"briefing": in_progress},
            )
        return redirect("dashboard:briefing")

    from briefings.models import BriefingConfig

    now = timezone.now()
    period_days = BriefingConfig.get().period_days

    # Fire a one-shot APScheduler job if the scheduler is running
    if _scheduler is not None and _scheduler.running:
        # Create the pending record so the UI can start polling immediately
        pending = WeeklyBriefing.objects.create(
            period_start=now - timezone.timedelta(days=period_days),
            period_end=now,
            model_used="",  # generator fills this in
            status=WeeklyBriefing.Status.PENDING,
        )
        # Use timezone.now() (timezone-aware) so APScheduler UTC clock matches
        _scheduler.add_job(
            weekly_briefing_job,
            trigger="date",
            run_date=now,
            args=[pending.pk],
            id=f"briefing_ondemand_{pending.pk}",
            name="AI Briefing (on-demand)",
            misfire_grace_time=300,
        )
        logger.info("Enqueued on-demand briefing job for pk=%d.", pending.pk)
    else:
        # Scheduler not running (e.g. dev with RUN_SCHEDULER=false) — run inline.
        # This blocks the request but keeps dev workflow simple.
        logger.warning("Scheduler not running — generating briefing synchronously.")
        from briefings.generator import BriefingGenerator

        pending = BriefingGenerator().run()

    if request.htmx:
        response = render(request, "partials/briefing_status.html", {"briefing": pending})
        # Synchronous path (scheduler off): briefing is already done — trigger full reload
        # so the main template re-renders with charts and final content.
        if not pending.is_in_progress:
            response["HX-Refresh"] = "true"
        return response
    return redirect("dashboard:briefing")


@login_required
def partial_briefing_status(request: HttpRequest, pk: int) -> HttpResponse:
    """
    HTMX polling endpoint for briefing generation status.

    Returns the briefing_status partial. While status is pending/generating,
    the partial re-attaches an hx-trigger to keep polling. Once ready or
    failed, polling stops.
    """
    from briefings.models import WeeklyBriefing

    b = get_object_or_404(WeeklyBriefing, pk=pk)
    b.refresh_from_db()
    response = render(request, "partials/briefing_status.html", {"briefing": b})
    # When generation finishes (ready or failed), trigger a full page reload so the
    # main briefing template re-renders with charts and final content.
    if not b.is_in_progress:
        response["HX-Refresh"] = "true"
    return response


@login_required
def briefing_settings(request: HttpRequest) -> HttpResponse:
    """
    GET/POST settings form for BriefingConfig and EmailConfig.

    Manages LLM provider selection (Ollama/Groq/Auto), model selection,
    Groq API key, data window, email recipients, and SMTP transport.
    """
    from briefings.models import BriefingConfig, EmailConfig

    config = BriefingConfig.get()
    email_cfg = EmailConfig.get()

    if request.method == "POST":
        # LLM provider and models
        config.llm_provider = request.POST.get("llm_provider", config.llm_provider).strip()
        config.ollama_model = request.POST.get("ollama_model", config.ollama_model).strip()
        config.groq_model = request.POST.get("groq_model", config.groq_model).strip()
        # Only update Groq API key when a new value is submitted; blank means "keep existing".
        new_groq_key = request.POST.get("groq_api_key", "")
        if new_groq_key:
            config.groq_api_key = new_groq_key

        try:
            config.period_days = max(1, int(request.POST.get("period_days", config.period_days)))
        except (ValueError, TypeError):
            pass
        config.send_briefing_email = "send_briefing_email" in request.POST
        config.briefing_email_recipients = request.POST.get("briefing_email_recipients", "").strip()
        config.save()

        email_cfg.smtp_host = request.POST.get("smtp_host", "").strip()
        try:
            email_cfg.smtp_port = int(request.POST.get("smtp_port", email_cfg.smtp_port))
        except (ValueError, TypeError):
            pass
        email_cfg.smtp_user = request.POST.get("smtp_user", "").strip()
        # Only update password when a new value is submitted; blank means "keep existing".
        new_password = request.POST.get("smtp_password", "")
        if new_password:
            email_cfg.smtp_password = new_password
        email_cfg.use_tls = "use_tls" in request.POST
        email_cfg.default_from_email = request.POST.get(
            "default_from_email", email_cfg.default_from_email
        ).strip()
        email_cfg.save()

        messages.success(request, "Briefing settings saved.")
        return redirect("dashboard:briefing_settings")

    # Mask the Groq API key for display (show last 4 chars if set)
    groq_key_masked = ""
    if config.groq_api_key:
        groq_key_masked = "\u2022" * 12 + config.groq_api_key[-4:]

    return render(
        request,
        "dashboard/briefing_settings.html",
        {
            "config": config,
            "email_cfg": email_cfg,
            "groq_key_masked": groq_key_masked,
            "llm_provider_choices": BriefingConfig.LLMProvider.choices,
        },
    )


@login_required
def briefing_models_json(request: HttpRequest) -> JsonResponse:
    """
    Return JSON lists of available LLM models for both Ollama and Groq.

    Ollama models are fetched live from the local service; Groq models are
    a static list of free-tier options. Both lists are always returned so
    the settings form can populate dropdowns regardless of selected provider.
    """
    import requests as http_requests
    from django.conf import settings

    from briefings.generator import GROQ_FREE_MODELS

    # Ollama: probe live service
    try:
        resp = http_requests.get(f"{settings.OLLAMA_BASE_URL}/api/tags", timeout=5)
        resp.raise_for_status()
        ollama_models = [m["name"] for m in resp.json().get("models", [])]
    except Exception as exc:
        logger.warning("Could not fetch Ollama models: %s", exc)
        ollama_models = []

    return JsonResponse(
        {
            "models": ollama_models,
            "groq_models": GROQ_FREE_MODELS,
        }
    )


# ---------------------------------------------------------------------------
# AI Observatory
# ---------------------------------------------------------------------------


@login_required
def ai_observatory(request: HttpRequest) -> HttpResponse:
    """AI Observatory: operational health and evolution of anomaly detection + GA systems."""
    from pathlib import Path

    from django.conf import settings
    from django.db.models import Avg

    from metrics.models import CompoundRule, DeltaThreshold, GARun

    # --- Anomaly Detection summary ---
    model_dir = Path(settings.ANOMALY_MODEL_DIR)
    model_files = list(model_dir.glob("*.joblib")) if model_dir.exists() else []

    anomaly_entity_ids: set[int] = set()
    anomaly_metric_keys: set[str] = set()
    for f in model_files:
        parts = f.stem.split("_", 1)
        if len(parts) == 2 and parts[0].isdigit():
            anomaly_entity_ids.add(int(parts[0]))
            anomaly_metric_keys.add(parts[1])

    dynamic_thresholds = DeltaThreshold.objects.filter(use_dynamic_threshold=True)
    static_thresholds = DeltaThreshold.objects.filter(use_dynamic_threshold=False)

    cutoff_30d = date.today() - timedelta(days=30)
    spike_alerts_30d = Alert.objects.filter(
        alert_type=Alert.AlertType.METRIC_SPIKE,
        created_at__date__gte=cutoff_30d,
    ).count()

    # --- GA summary ---
    ga_runs = list(GARun.objects.order_by("-started_at")[:10])
    latest_ga = ga_runs[0] if ga_runs else None
    active_rules = CompoundRule.objects.filter(is_active=True)

    compound_alerts_30d = Alert.objects.filter(
        alert_type=Alert.AlertType.COMPOUND_SPIKE,
        created_at__date__gte=cutoff_30d,
    ).count()

    labels_positive = AlertSeen.objects.filter(useful=True).count()
    labels_negative = AlertSeen.objects.filter(useful=False).count()
    labels_neutral = AlertSeen.objects.filter(useful__isnull=True).count()
    total_dismissed = labels_positive + labels_negative + labels_neutral

    rule_stats = active_rules.aggregate(
        avg_precision=Avg("precision"),
        avg_recall=Avg("recall"),
        avg_f1=Avg("f1_score"),
    )

    context = {
        # Anomaly Detection
        "total_models": len(model_files),
        "entities_covered": len(anomaly_entity_ids),
        "metrics_covered": len(anomaly_metric_keys),
        "dynamic_count": dynamic_thresholds.count(),
        "static_count": static_thresholds.count(),
        "spike_alerts_30d": spike_alerts_30d,
        # GA
        "ga_runs": ga_runs,
        "latest_ga": latest_ga,
        "active_rules": active_rules,
        "active_rule_count": active_rules.count(),
        "compound_alerts_30d": compound_alerts_30d,
        "total_dismissed": total_dismissed,
        "labels_positive": labels_positive,
        "labels_negative": labels_negative,
        "labels_neutral": labels_neutral,
        "avg_precision": (rule_stats["avg_precision"] or 0) * 100,
        "avg_recall": (rule_stats["avg_recall"] or 0) * 100,
        "avg_f1": rule_stats["avg_f1"] or 0,
        # Shared
        "unseen_alert_count": _unseen_count(request.user),
    }
    return render(request, "dashboard/ai_observatory.html", context)


@login_required
def ai_anomaly_data(request: HttpRequest) -> JsonResponse:
    """JSON for anomaly detection charts: severity timeline + model coverage."""
    from collections import Counter
    from pathlib import Path

    from django.conf import settings

    # Severity timeline: weekly METRIC_SPIKE alerts by severity, last 90 days
    cutoff = date.today() - timedelta(days=90)
    spikes_qs = Alert.objects.filter(
        alert_type=Alert.AlertType.METRIC_SPIKE,
        created_at__date__gte=cutoff,
    )
    # Respect watchlist scope so the anomaly chart reflects the user's monitored entities
    watched_slugs = _get_watched_slugs(request.user)
    if watched_slugs is not None:
        spikes_qs = spikes_qs.filter(entity__slug__in=watched_slugs)
    spikes = spikes_qs.values_list("created_at", "metadata")

    weekly_severity: dict[str, dict[str, int]] = defaultdict(
        lambda: {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    )
    for created_at, metadata in spikes:
        week_label = created_at.strftime("%Y-W%W")
        severity = metadata.get("severity", "LOW") if isinstance(metadata, dict) else "LOW"
        weekly_severity[week_label][severity] += 1

    weeks = sorted(weekly_severity.keys())

    # Model coverage: count of entity models per metric_key
    model_dir = Path(settings.ANOMALY_MODEL_DIR)
    key_counts: Counter[str] = Counter()
    if model_dir.exists():
        for f in model_dir.glob("*.joblib"):
            parts = f.stem.split("_", 1)
            if len(parts) == 2:
                key_counts[parts[1]] += 1

    model_keys = sorted(key_counts.keys())

    return JsonResponse(
        {
            "severity_timeline": {
                "labels": weeks,
                "high": [weekly_severity[w]["HIGH"] for w in weeks],
                "medium": [weekly_severity[w]["MEDIUM"] for w in weeks],
                "low": [weekly_severity[w]["LOW"] for w in weeks],
            },
            "model_coverage": {
                "labels": model_keys,
                "counts": [key_counts[k] for k in model_keys],
            },
        }
    )


@login_required
def ai_ga_data(request: HttpRequest) -> JsonResponse:
    """JSON for GA charts: fitness across runs + convergence curve."""
    from metrics.models import GARun

    runs = (
        GARun.objects.filter(
            status__in=[GARun.Status.SUCCESS, GARun.Status.NO_RULES],
            best_fitness__isnull=False,
        )
        .order_by("started_at")
        .values(
            "pk",
            "started_at",
            "best_fitness",
            "rules_produced",
            "labeled_positives",
            "fitness_history",
        )
    )

    run_labels: list[str] = []
    run_fitness: list[float] = []
    run_rules_produced: list[int] = []
    run_labeled: list[int] = []
    convergence_labels: list[int] = []
    convergence_values: list[float] = []

    for r in runs:
        run_labels.append(r["started_at"].strftime("%Y-%m-%d"))
        run_fitness.append(r["best_fitness"] or 0)
        run_rules_produced.append(r["rules_produced"] or 0)
        run_labeled.append(r["labeled_positives"] or 0)

        # Use the most recent run's fitness_history for convergence chart
        if r["fitness_history"]:
            convergence_values = r["fitness_history"]
            convergence_labels = list(range(1, len(convergence_values) + 1))

    return JsonResponse(
        {
            "fitness_over_time": {
                "labels": run_labels,
                "best_fitness": run_fitness,
                "rules_produced": run_rules_produced,
                "labeled_positives": run_labeled,
            },
            "convergence": {
                "labels": convergence_labels,
                "values": convergence_values,
            },
        }
    )


@login_required
def ai_alert_volume_data(request: HttpRequest) -> JsonResponse:
    """JSON for alert volume comparison chart (METRIC_SPIKE vs COMPOUND_SPIKE)."""

    cutoff = date.today() - timedelta(days=90)

    alerts_qs = Alert.objects.filter(
        alert_type__in=[Alert.AlertType.METRIC_SPIKE, Alert.AlertType.COMPOUND_SPIKE],
        created_at__date__gte=cutoff,
    )
    # Respect watchlist scope so the volume chart reflects the user's monitored entities
    watched_slugs = _get_watched_slugs(request.user)
    if watched_slugs is not None:
        alerts_qs = alerts_qs.filter(entity__slug__in=watched_slugs)
    alerts = alerts_qs.values_list("pk", "alert_type", "created_at")

    dismissed_ids = set(AlertSeen.objects.values_list("alert_id", flat=True))

    weekly: dict[str, dict[str, int]] = defaultdict(
        lambda: {"metric_spike": 0, "compound_spike": 0, "dismissed": 0}
    )
    for pk, atype, created_at in alerts:
        week = created_at.strftime("%Y-W%W")
        weekly[week][atype] += 1
        if pk in dismissed_ids:
            weekly[week]["dismissed"] += 1

    weeks = sorted(weekly.keys())

    return JsonResponse(
        {
            "labels": weeks,
            "metric_spike": [weekly[w]["metric_spike"] for w in weeks],
            "compound_spike": [weekly[w]["compound_spike"] for w in weeks],
            "dismissed": [weekly[w]["dismissed"] for w in weeks],
        }
    )


@login_required
def ai_threshold_history(request: HttpRequest) -> JsonResponse:
    """JSON for threshold evolution chart: single line per selected entity/metric combo.

    Query params:
        metric_key  — filter to one metric (required for a meaningful chart)
        entity_id   — filter to one entity (omit for global/legacy snapshots)

    Also returns the full list of metric keys and tracked entities so the
    frontend can populate its dropdowns on the first request.
    """
    from core.models import Entity
    from metrics.models import ThresholdSnapshot

    metric_key = request.GET.get("metric_key", "")
    entity_id_param = request.GET.get("entity_id", "")

    qs = ThresholdSnapshot.objects.order_by("captured_at")
    if metric_key:
        qs = qs.filter(metric_key=metric_key)
    if entity_id_param:
        entity_id = int(entity_id_param)
        # Prefer per-entity snapshots; fall back to global (entity=None)
        # when the entity has none yet (pre-migration data).
        entity_qs = qs.filter(entity_id=entity_id)
        qs = entity_qs if entity_qs.exists() else qs.filter(entity__isnull=True)
    else:
        qs = qs.filter(entity__isnull=True)

    labels: list[str] = []
    values: list[float] = []
    sources: list[str] = []
    for snap in qs.values("effective_threshold", "source", "captured_at"):
        labels.append(snap["captured_at"].strftime("%Y-%m-%d"))
        values.append(round(snap["effective_threshold"] * 100, 2))
        sources.append(snap["source"])

    # Dropdown options — .order_by() clears model ordering so DISTINCT works
    # (the default Meta ordering on captured_at was getting added to SELECT,
    # making every row "distinct").
    all_keys = sorted(
        ThresholdSnapshot.objects.order_by().values_list("metric_key", flat=True).distinct()
    )
    entities = list(Entity.objects.order_by("name").values("id", "name"))

    return JsonResponse(
        {
            "metric_keys": all_keys,
            "entities": entities,
            "labels": labels,
            "values": values,
            "sources": sources,
        }
    )


@login_required
def ai_threshold_evolution_page(request: HttpRequest) -> HttpResponse:
    """Dedicated Plotly-based page for the threshold evolution explorer."""
    return render(
        request,
        "dashboard/ai_observatory_v2.html",
        {"unseen_alert_count": _unseen_count(request.user)},
    )


@login_required
def ai_threshold_evolution_data(request: HttpRequest) -> JsonResponse:
    """JSON data for the Plotly threshold evolution page.

    Returns one series per entity so the frontend renders one trace per entity.
    Query params:
        metric_key — filter to one metric (defaults to the first available key)
    """
    from core.models import Entity
    from metrics.models import ThresholdSnapshot

    metric_key = request.GET.get("metric_key", "")
    all_keys = sorted(
        ThresholdSnapshot.objects.order_by().values_list("metric_key", flat=True).distinct()
    )
    if not metric_key and all_keys:
        metric_key = all_keys[0]

    series: list[dict] = []
    if metric_key:
        for entity in Entity.objects.order_by("name"):
            qs = ThresholdSnapshot.objects.filter(metric_key=metric_key, entity=entity).order_by(
                "captured_at"
            )
            if not qs.exists():
                continue
            dates: list[str] = []
            values: list[float] = []
            sources: list[str] = []
            for snap in qs.values("effective_threshold", "source", "captured_at"):
                dates.append(snap["captured_at"].strftime("%Y-%m-%d"))
                values.append(round(snap["effective_threshold"] * 100, 2))
                sources.append(snap["source"])
            series.append(
                {
                    "entity_id": entity.id,
                    "entity_name": entity.name,
                    "dates": dates,
                    "values": values,
                    "sources": sources,
                }
            )

    return JsonResponse(
        {
            "metric_key": metric_key,
            "metric_keys": all_keys,
            "series": series,
        }
    )


# ---------------------------------------------------------------------------
# Momentum Leaderboard
# ---------------------------------------------------------------------------


@login_required
def momentum_leaderboard(request: HttpRequest) -> HttpResponse:
    """
    Full-page momentum leaderboard: entities ranked by composite momentum score.

    Shows a ranked table with score, acceleration, metric coverage, and a
    Chart.js time-series of the top entities' momentum over time.
    """
    # Latest score per entity (most recent computed_at batch)
    latest_batch = (
        MomentumScore.objects.order_by("-computed_at").values_list("computed_at", flat=True).first()
    )

    scores: list[MomentumScore] = []
    if latest_batch:
        scores = list(
            MomentumScore.objects.filter(computed_at=latest_batch)
            .select_related("entity")
            .order_by("-score")
        )

    # Total possible metrics (for coverage display)
    total_metrics = len(METRIC_LABELS)

    # Build scatter data (speed vs acceleration) for inline JSON
    scatter_data = []
    for ms in scores:
        if ms.acceleration is not None:
            scatter_data.append(
                {
                    "x": round(ms.score, 4),
                    "y": round(ms.acceleration, 4),
                    "name": ms.entity.name,
                    "slug": ms.entity.slug,
                    "is_primary": ms.entity.is_primary,
                    "coverage": ms.metrics_used,
                }
            )

    context = {
        "scores": scores,
        "total_metrics": total_metrics,
        "unseen_alert_count": _unseen_count(request.user),
        "scatter_data_json": json.dumps(scatter_data),
    }
    return render(request, "dashboard/momentum.html", context)


@login_required
def momentum_chart_data(request: HttpRequest) -> JsonResponse:
    """
    JSON endpoint: momentum score time-series for Chart.js.

    Returns ALL entities with momentum history (up to 90 days), sorted by
    latest score descending. The template handles visibility toggling.
    """
    latest_batch = (
        MomentumScore.objects.order_by("-computed_at").values_list("computed_at", flat=True).first()
    )

    if not latest_batch:
        return JsonResponse({"labels": [], "datasets": []})

    # All entities that have scores, ordered by latest score
    all_entity_ids = list(
        MomentumScore.objects.filter(computed_at=latest_batch)
        .order_by("-score")
        .values_list("entity_id", flat=True)
    )
    top_entity_ids = all_entity_ids

    cutoff = timezone.now() - timedelta(days=90)

    # Extended color palette for all entities
    colors = [
        "#f7931a",
        "#3b82f6",
        "#10b981",
        "#f59e0b",
        "#ef4444",
        "#8b5cf6",
        "#ec4899",
        "#06b6d4",
        "#84cc16",
        "#f97316",
        "#14b8a6",
        "#a855f7",
        "#e11d48",
        "#0ea5e9",
        "#65a30d",
        "#d946ef",
        "#f43f5e",
        "#22d3ee",
        "#facc15",
        "#fb923c",
        "#4ade80",
        "#818cf8",
        "#fb7185",
        "#2dd4bf",
        "#a3e635",
        "#c084fc",
        "#fbbf24",
        "#34d399",
        "#60a5fa",
        "#f472b6",
        "#38bdf8",
        "#a78bfa",
        "#fca5a5",
    ]

    datasets = []
    all_dates: set[str] = set()

    for idx, entity_id in enumerate(top_entity_ids):
        history = list(
            MomentumScore.objects.filter(entity_id=entity_id, computed_at__gte=cutoff)
            .order_by("computed_at")
            .select_related("entity")
            .values_list("computed_at", "score", "entity__name")
        )
        if not history:
            continue

        dates = [ts.strftime("%Y-%m-%d") for ts, _, _ in history]
        values = [round(float(s), 4) for _, s, _ in history]
        name = history[0][2]
        all_dates.update(dates)

        datasets.append(
            {
                "label": name,
                "data": values,
                "dates": dates,
                "borderColor": colors[idx % len(colors)],
                "backgroundColor": colors[idx % len(colors)] + "20",
                "borderWidth": 2,
                "pointRadius": 1,
                "tension": 0.3,
                "fill": False,
                "hidden": idx >= 5,
            }
        )

    labels = sorted(all_dates)
    return JsonResponse({"labels": labels, "datasets": datasets})


# ---------------------------------------------------------------------------
# News Intelligence
# ---------------------------------------------------------------------------

# Sentiment to numeric score mapping for heatmap aggregation
_SENTIMENT_SCORES = {"positive": 1.0, "neutral": 0.0, "negative": -1.0, "mixed": 0.5}

# Signal type display labels
_SIGNAL_TYPE_LABELS = {
    "product_launch": "Product Launch",
    "partnership": "Partnership",
    "funding": "Funding",
    "vulnerability": "Vulnerability",
    "regulation": "Regulation",
    "hiring": "Hiring",
    "opinion": "Opinion",
    "other": "Other",
}


@login_required
def news_intelligence(request: HttpRequest) -> HttpResponse:
    """
    News Intelligence page: LLM-enriched articles with sentiment badges,
    signal classification, filters, heatmap, and on-demand key takeaways.
    """
    # Read filter params
    sentiment_filter = request.GET.get("sentiment", "")
    signal_filter = request.GET.get("signal", "")
    entity_slug = request.GET.get("entity", "")
    days = int(request.GET.get("days", "30"))
    days = min(max(days, 7), 365)  # clamp to 7-365

    watched_slugs = _get_watched_slugs(request.user)

    # Base queryset: only enriched articles
    cutoff = timezone.now() - timedelta(days=days)
    qs = (
        NewsItem.objects.filter(
            enriched_at__isnull=False,
            published_at__gte=cutoff,
        )
        .prefetch_related("entities")
        .order_by("-published_at")
    )

    # Scope to watchlist
    if watched_slugs is not None:
        qs = qs.filter(entities__slug__in=watched_slugs).distinct()

    # Apply server-side filters
    if sentiment_filter and sentiment_filter in _SENTIMENT_SCORES:
        qs = qs.filter(sentiment=sentiment_filter)
    if signal_filter and signal_filter in _SIGNAL_TYPE_LABELS:
        qs = qs.filter(signal_type=signal_filter)
    if entity_slug:
        qs = qs.filter(entities__slug=entity_slug)

    news_items = list(qs[:200])

    # Entity list for the filter dropdown
    entities = Entity.objects.order_by("name")
    if watched_slugs is not None:
        entities = entities.filter(slug__in=watched_slugs)

    # Build sentiment heatmap: entities × weeks
    heatmap = _build_sentiment_heatmap(days, watched_slugs)

    # Compute breakdown stats server-side
    breakdown = _build_sentiment_breakdown(news_items)

    context = {
        "news_items": news_items,
        "entities": entities,
        "heatmap_json": json.dumps(heatmap),
        "breakdown": breakdown,
        "current_sentiment": sentiment_filter,
        "current_signal": signal_filter,
        "current_entity": entity_slug,
        "current_days": days,
        "signal_type_labels": _SIGNAL_TYPE_LABELS,
        "unseen_alert_count": _unseen_count(request.user),
    }
    return render(request, "dashboard/news_intelligence.html", context)


@login_required
@require_POST
def news_key_takeaways(request: HttpRequest) -> HttpResponse:
    """HTMX endpoint: generate on-demand key takeaways from enriched articles."""
    from metrics.news_enrichment import generate_key_takeaways

    days = int(request.POST.get("days", "7"))
    days = min(max(days, 1), 30)

    html = generate_key_takeaways(days=days)
    return render(request, "partials/news_takeaways.html", {"takeaways_html": html})


def _build_sentiment_heatmap(
    days: int,
    watched_slugs: set[str] | None,
) -> dict:
    """
    Build a sentiment heatmap: entities (rows) × ISO weeks (columns).

    Returns a dict with:
    - ``entities``: list of entity names (row labels)
    - ``weeks``: list of ISO week labels (column headers)
    - ``cells``: list of lists, each inner list is one row of score values
      (float or None for empty cells)
    """
    cutoff = timezone.now() - timedelta(days=days)

    qs = NewsItem.objects.filter(
        enriched_at__isnull=False,
        published_at__gte=cutoff,
        sentiment__in=["positive", "neutral", "negative", "mixed"],
    ).prefetch_related("entities")

    if watched_slugs is not None:
        qs = qs.filter(entities__slug__in=watched_slugs)

    # Accumulate scores per (entity_slug, iso_week)
    scores: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    entity_names: dict[str, str] = {}
    all_weeks: set[str] = set()

    for item in qs:
        score = _SENTIMENT_SCORES.get(item.sentiment)
        if score is None:
            continue
        week_label = item.published_at.strftime("%Y-W%V")
        all_weeks.add(week_label)
        for entity in item.entities.all():
            scores[entity.slug][week_label].append(score)
            entity_names[entity.slug] = entity.name

    if not scores:
        return {"entities": [], "weeks": [], "cells": []}

    # Sort weeks chronologically, limit entities to top 15 by article count
    sorted_weeks = sorted(all_weeks)
    entity_counts = {
        slug: sum(len(v) for v in week_scores.values()) for slug, week_scores in scores.items()
    }
    top_slugs = sorted(entity_counts, key=entity_counts.get, reverse=True)[:15]

    # Build cell grid: average sentiment per (entity, week)
    cells = []
    entity_labels = []
    for slug in top_slugs:
        entity_labels.append(entity_names[slug])
        row = []
        for week in sorted_weeks:
            week_scores = scores[slug].get(week, [])
            if week_scores:
                row.append(round(sum(week_scores) / len(week_scores), 2))
            else:
                row.append(None)
        cells.append(row)

    return {
        "entities": entity_labels,
        "weeks": sorted_weeks,
        "cells": cells,
    }


def _build_sentiment_breakdown(news_items: list) -> list[dict]:
    """
    Compute sentiment distribution from a list of enriched NewsItems.

    Returns a list of dicts with keys: label, count, pct, color.
    """
    counts: dict[str, int] = {"positive": 0, "neutral": 0, "negative": 0, "mixed": 0}
    for item in news_items:
        if item.sentiment in counts:
            counts[item.sentiment] += 1

    total = sum(counts.values()) or 1
    colors = {
        "positive": "#22c55e",
        "neutral": "#6b7280",
        "negative": "#ef4444",
        "mixed": "#eab308",
    }
    text_colors = {
        "positive": "#4ade80",
        "neutral": "#9ca3af",
        "negative": "#f87171",
        "mixed": "#facc15",
    }

    return [
        {
            "label": label,
            "count": count,
            "pct": round(count / total * 100),
            "color": colors[label],
            "text_color": text_colors[label],
        }
        for label, count in counts.items()
    ]

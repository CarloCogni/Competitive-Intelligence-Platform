# dashboard/tests/test_views.py
"""Tests for dashboard views."""

import json
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from freezegun import freeze_time

from core.tests.factories import EntityFactory, VerticalFactory
from metrics.models import Alert, AlertSeen
from metrics.tests.factories import (
    AlertFactory,
    CandidateEntityFactory,
    MetricFactory,
    NewsItemFactory,
)


@pytest.fixture
def client(db):
    """Authenticated Django test client (most views require @login_required)."""
    user = User.objects.create_user(username="testuser", password="testpass")
    # Signal auto-creates UserProfile; set show_all_entities so views don't
    # filter by an empty watchlist.
    user.profile.show_all_entities = True
    user.profile.save(update_fields=["show_all_entities"])
    c = Client()
    c.login(username="testuser", password="testpass")
    c._user = user  # stash for tests that need the user object
    return c


@pytest.fixture
def entity(db):
    return EntityFactory(slug="test-entity")


@pytest.fixture
def entity_with_metrics(db):
    e = EntityFactory(slug="entity-with-metrics")
    MetricFactory(entity=e, key="github_stars", value=1000.0)
    MetricFactory(entity=e, key="appstore_rating", value=4.5)
    return e


@pytest.mark.django_db
class TestDashboardView:
    def test_returns_200(self, client, entity):
        url = reverse("dashboard:main_dashboard")
        response = client.get(url)
        assert response.status_code == 200

    def test_entities_in_context(self, client, entity):
        url = reverse("dashboard:main_dashboard")
        response = client.get(url)
        assert entity in response.context["entities"]

    def test_kpi_counts_in_context(self, client, entity):
        url = reverse("dashboard:main_dashboard")
        response = client.get(url)
        assert "total_count" in response.context
        assert "unseen_alert_count" in response.context


@pytest.mark.django_db
class TestEntityDetailView:
    def test_returns_200(self, client, entity):
        url = reverse("dashboard:entity_detail", kwargs={"slug": entity.slug})
        response = client.get(url)
        assert response.status_code == 200

    def test_entity_in_context(self, client, entity):
        url = reverse("dashboard:entity_detail", kwargs={"slug": entity.slug})
        response = client.get(url)
        assert response.context["entity"] == entity

    def test_404_for_unknown_slug(self, client):
        url = reverse("dashboard:entity_detail", kwargs={"slug": "does-not-exist"})
        response = client.get(url)
        assert response.status_code == 404

    def test_latest_metrics_in_context(self, client, entity_with_metrics):
        url = reverse("dashboard:entity_detail", kwargs={"slug": entity_with_metrics.slug})
        response = client.get(url)
        assert "github_stars" in response.context["latest_metrics"]
        assert response.context["latest_metrics"]["github_stars"] == 1000.0

    def test_recent_news_in_context(self, client, entity):
        item = NewsItemFactory()
        item.entities.add(entity)
        url = reverse("dashboard:entity_detail", kwargs={"slug": entity.slug})
        response = client.get(url)
        assert item in response.context["recent_news"]

    def test_recent_alerts_in_context(self, client, entity):
        alert = AlertFactory(entity=entity)
        url = reverse("dashboard:entity_detail", kwargs={"slug": entity.slug})
        response = client.get(url)
        assert alert in response.context["recent_alerts"]


@pytest.mark.django_db
class TestHeadToHeadView:
    def test_returns_200(self, client, db):
        url = reverse("dashboard:head_to_head")
        response = client.get(url)
        assert response.status_code == 200

    def test_entities_in_context(self, client, entity):
        url = reverse("dashboard:head_to_head")
        response = client.get(url)
        assert entity in response.context["entities"]


@pytest.mark.django_db
class TestFeedView:
    def test_returns_200(self, client, db):
        url = reverse("dashboard:feed")
        response = client.get(url)
        assert response.status_code == 200

    def test_news_items_in_context(self, client, db):
        item = NewsItemFactory()
        url = reverse("dashboard:feed")
        response = client.get(url)
        assert item in response.context["news_items"]

    def test_entity_filter(self, client, db):
        e1 = EntityFactory(slug="filter-a")
        e2 = EntityFactory(slug="filter-b")
        item1 = NewsItemFactory(url="https://example.com/item1")
        item2 = NewsItemFactory(url="https://example.com/item2")
        item1.entities.add(e1)
        item2.entities.add(e2)

        url = reverse("dashboard:feed") + "?entity=filter-a"
        response = client.get(url)
        assert item1 in response.context["news_items"]
        assert item2 not in response.context["news_items"]


@pytest.mark.django_db
class TestAlertsView:
    def test_returns_200(self, client, db):
        url = reverse("dashboard:alerts")
        response = client.get(url)
        assert response.status_code == 200

    def test_alerts_in_context(self, client, db):
        alert = AlertFactory()
        url = reverse("dashboard:alerts")
        response = client.get(url)
        assert alert in response.context["alerts"]


@pytest.mark.django_db
class TestPartialEndpoints:
    def test_entity_table_partial_returns_200(self, client, entity):
        url = reverse("dashboard:partial_entity_table")
        response = client.get(url)
        assert response.status_code == 200

    def test_alerts_partial_returns_200(self, client, db):
        url = reverse("dashboard:partial_alerts")
        response = client.get(url)
        assert response.status_code == 200

    def test_comparison_partial_with_two_entities(self, client, db):
        e1 = EntityFactory(slug="comp-a")
        e2 = EntityFactory(slug="comp-b")
        url = reverse("dashboard:partial_comparison") + f"?a={e1.slug}&b={e2.slug}"
        response = client.get(url)
        assert response.status_code == 200

    def test_comparison_partial_empty_slugs(self, client, db):
        url = reverse("dashboard:partial_comparison")
        response = client.get(url)
        assert response.status_code == 200

    def test_sparkline_returns_json(self, client, entity_with_metrics):
        url = reverse(
            "dashboard:partial_sparkline",
            kwargs={"entity_slug": entity_with_metrics.slug, "metric_key": "github_stars"},
        )
        response = client.get(url)
        assert response.status_code == 200
        data = json.loads(response.content)
        assert "labels" in data
        assert "values" in data
        assert data["values"][0] == 1000.0

    def test_sparkline_404_for_bad_entity(self, client, db):
        url = reverse(
            "dashboard:partial_sparkline",
            kwargs={"entity_slug": "no-such-entity", "metric_key": "github_stars"},
        )
        response = client.get(url)
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Landing pages (public, no auth required)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestLandingViews:
    """Tests for landing page views — unauthenticated renders and authenticated redirects."""

    def test_landing_default_unauthenticated(self, db):
        c = Client()
        response = c.get(reverse("dashboard:landing"))
        assert response.status_code == 200

    def test_landing_default_authenticated_redirects(self, client):
        response = client.get(reverse("dashboard:landing"))
        assert response.status_code == 302
        assert "/dashboard/" in response.url


# ---------------------------------------------------------------------------
# Vertical detail
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestVerticalDetailView:
    def test_returns_200(self, client):
        v = VerticalFactory(slug="test-vert")
        EntityFactory(vertical=v)
        url = reverse("dashboard:vertical_detail", kwargs={"slug": "test-vert"})
        response = client.get(url)
        assert response.status_code == 200

    def test_404_for_unknown_slug(self, client):
        url = reverse("dashboard:vertical_detail", kwargs={"slug": "nonexistent"})
        response = client.get(url)
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Alert management POST views
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMarkAlertSeen:
    def test_post_creates_alert_seen(self, client):
        alert = AlertFactory()
        url = reverse("dashboard:mark_alert_seen", kwargs={"pk": alert.pk})
        response = client.post(url)
        assert response.status_code == 200
        assert AlertSeen.objects.filter(user=client._user, alert=alert).exists()

    def test_get_returns_405(self, client):
        alert = AlertFactory()
        url = reverse("dashboard:mark_alert_seen", kwargs={"pk": alert.pk})
        response = client.get(url)
        assert response.status_code == 405

    def test_unauthenticated_redirects(self, db):
        alert = AlertFactory()
        c = Client()
        url = reverse("dashboard:mark_alert_seen", kwargs={"pk": alert.pk})
        response = c.post(url)
        assert response.status_code == 302
        assert "/accounts/login/" in response.url


@pytest.mark.django_db
class TestMarkAllAlertsSeen:
    def test_post_marks_all(self, client):
        AlertFactory()
        AlertFactory()
        url = reverse("dashboard:mark_all_alerts_seen")
        response = client.post(url)
        assert response.status_code == 200
        assert AlertSeen.objects.filter(user=client._user).count() == 2


@pytest.mark.django_db
class TestDeleteAlert:
    def test_post_deletes_alert(self, client):
        alert = AlertFactory()
        url = reverse("dashboard:delete_alert", kwargs={"pk": alert.pk})
        response = client.post(url)
        assert response.status_code == 200
        assert not Alert.objects.filter(pk=alert.pk).exists()


@pytest.mark.django_db
class TestClearSeenAlerts:
    def test_clears_seen_alerts(self, client):
        alert = AlertFactory()
        AlertSeen.objects.create(user=client._user, alert=alert)
        url = reverse("dashboard:clear_seen_alerts")
        response = client.post(url)
        assert response.status_code == 200
        assert not Alert.objects.filter(pk=alert.pk).exists()


# ---------------------------------------------------------------------------
# Watchlist and scope toggles
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestToggleWatch:
    def test_adds_entity_to_watchlist(self, client):
        e = EntityFactory(slug="watch-me")
        url = reverse("dashboard:toggle_watch", kwargs={"slug": e.slug})
        response = client.post(url)
        assert response.status_code == 200
        assert client._user.profile.watched_entities.filter(pk=e.pk).exists()

    def test_removes_entity_from_watchlist(self, client):
        e = EntityFactory(slug="unwatch-me")
        client._user.profile.watched_entities.add(e)
        url = reverse("dashboard:toggle_watch", kwargs={"slug": e.slug})
        response = client.post(url)
        assert response.status_code == 200
        assert not client._user.profile.watched_entities.filter(pk=e.pk).exists()


@pytest.mark.django_db
class TestToggleScope:
    def test_toggles_show_all(self, client):
        assert client._user.profile.show_all_entities is True
        url = reverse("dashboard:toggle_scope")
        response = client.post(url)
        assert response.status_code == 302
        client._user.profile.refresh_from_db()
        assert client._user.profile.show_all_entities is False


# ---------------------------------------------------------------------------
# Save comparison
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSaveComparison:
    def test_saves_comparison(self, client):
        e1 = EntityFactory(slug="save-a")
        e2 = EntityFactory(slug="save-b")
        url = reverse("dashboard:save_comparison")
        response = client.post(url, {"name": "test", "entity_a": e1.slug, "entity_b": e2.slug})
        assert response.status_code == 200

    def test_missing_fields_returns_400(self, client):
        url = reverse("dashboard:save_comparison")
        response = client.post(url, {"name": "", "entity_a": "", "entity_b": ""})
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# Scheduler views
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSchedulerViews:
    def test_scheduler_status_returns_200(self, client):
        url = reverse("dashboard:scheduler_status")
        response = client.get(url)
        assert response.status_code == 200

    def test_scheduler_chart_data_returns_json(self, client):
        url = reverse("dashboard:scheduler_chart_data")
        response = client.get(url)
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Candidate views
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCandidateViews:
    def test_candidate_list_returns_200(self, client):
        url = reverse("dashboard:candidate_list")
        response = client.get(url)
        assert response.status_code == 200

    def test_dismiss_candidate(self, client):
        candidate = CandidateEntityFactory()
        url = reverse("dashboard:dismiss_candidate", kwargs={"pk": candidate.pk})
        response = client.post(url)
        assert response.status_code == 302  # redirects to candidate_list

    def test_dismiss_all_candidates(self, client):
        CandidateEntityFactory()
        url = reverse("dashboard:dismiss_all_candidates")
        response = client.post(url)
        assert response.status_code == 302  # redirects to candidate_list


# ---------------------------------------------------------------------------
# Additional partial endpoints
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAdditionalPartials:
    def test_ranking_chart_returns_200(self, client, db):
        url = reverse("dashboard:partial_ranking_chart")
        response = client.get(url)
        assert response.status_code == 200

    def test_news_histogram_returns_200(self, client, entity):
        url = reverse("dashboard:partial_news_histogram", kwargs={"entity_slug": entity.slug})
        response = client.get(url)
        assert response.status_code == 200

    def test_dual_trend_returns_200(self, client, db):
        e1 = EntityFactory(slug="trend-a")
        e2 = EntityFactory(slug="trend-b")
        url = reverse("dashboard:partial_dual_trend") + f"?a={e1.slug}&b={e2.slug}"
        response = client.get(url)
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Profile settings
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestProfileSettings:
    def test_get_returns_200(self, client):
        url = reverse("dashboard:profile_settings")
        response = client.get(url)
        assert response.status_code == 200

    def test_post_saves_and_redirects(self, client):
        url = reverse("dashboard:profile_settings")
        response = client.post(url, {"timezone": "UTC", "digest_enabled": True})
        assert response.status_code == 302


# ---------------------------------------------------------------------------
# Briefing views
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBriefingViews:
    def test_briefing_page_returns_200(self, client):
        url = reverse("dashboard:briefing")
        response = client.get(url)
        assert response.status_code == 200

    def test_briefing_settings_returns_200(self, client):
        url = reverse("dashboard:briefing_settings")
        response = client.get(url)
        assert response.status_code == 200

    def test_briefing_models_json_returns_200(self, client):
        url = reverse("dashboard:briefing_models_json")
        response = client.get(url)
        assert response.status_code == 200

    def test_briefing_generate_post(self, client):
        url = reverse("dashboard:briefing_generate")
        response = client.post(url)
        # Either 200 (inline gen) or 302 (redirect) depending on scheduler state
        assert response.status_code in (200, 302)

    def test_mark_entity_alerts_seen(self, client):
        e = EntityFactory(slug="mark-entity")
        AlertFactory(entity=e)
        url = reverse("dashboard:mark_entity_alerts_seen", kwargs={"slug": e.slug})
        response = client.post(url)
        assert response.status_code == 200

    def test_promote_candidate(self, client):
        candidate = CandidateEntityFactory()
        url = reverse("dashboard:promote_candidate", kwargs={"pk": candidate.pk})
        response = client.post(url)
        assert response.status_code == 302

    def test_briefing_settings_post_saves(self, client):
        url = reverse("dashboard:briefing_settings")
        response = client.post(
            url,
            {
                "llm_provider": "ollama",
                "ollama_model": "gemma4:e4b",
                "groq_model": "llama-3.3-70b-versatile",
                "period_days": "14",
                "smtp_host": "",
                "smtp_port": "587",
                "smtp_user": "",
                "default_from_email": "test@example.com",
            },
        )
        assert response.status_code == 302

    def test_briefing_with_existing_briefing(self, client):
        from django.utils import timezone as tz

        from briefings.models import WeeklyBriefing

        WeeklyBriefing.objects.create(
            period_start=tz.now(),
            period_end=tz.now(),
            status=WeeklyBriefing.Status.READY,
            content="# Test",
        )
        url = reverse("dashboard:briefing")
        response = client.get(url)
        assert response.status_code == 200

    def test_partial_briefing_status(self, client):
        from django.utils import timezone as tz

        from briefings.models import WeeklyBriefing

        b = WeeklyBriefing.objects.create(
            period_start=tz.now(),
            period_end=tz.now(),
            status=WeeklyBriefing.Status.READY,
        )
        url = reverse("dashboard:partial_briefing_status", kwargs={"pk": b.pk})
        response = client.get(url)
        assert response.status_code == 200

    def test_dashboard_returns_200(self, client, entity):
        url = reverse("dashboard:main_dashboard")
        response = client.get(url)
        assert response.status_code == 200

    def test_alerts_htmx_request(self, client):
        AlertFactory()
        url = reverse("dashboard:alerts")
        response = client.get(url, HTTP_HX_REQUEST="true")
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Landscape view
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestLandscapeView:
    def test_returns_200(self, client, entity):
        url = reverse("dashboard:landscape")
        response = client.get(url)
        assert response.status_code == 200

    def test_graph_data_in_context(self, client, entity):
        url = reverse("dashboard:landscape")
        response = client.get(url)
        assert "graph_data" in response.context


# ---------------------------------------------------------------------------
# News intelligence views
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestNewsIntelligenceView:
    def test_returns_200(self, client, db):
        url = reverse("dashboard:news_intelligence")
        response = client.get(url)
        assert response.status_code == 200

    def test_news_items_in_context(self, client, db):
        NewsItemFactory(sentiment="positive")
        url = reverse("dashboard:news_intelligence")
        response = client.get(url)
        assert "news_items" in response.context


@pytest.mark.django_db
class TestNewsKeyTakeawaysView:
    @pytest.fixture(autouse=True)
    def _patch_takeaways(self):
        with patch(
            "metrics.news_enrichment.generate_key_takeaways",
            return_value="<ul><li>Takeaway</li></ul>",
        ):
            yield

    def test_post_returns_200(self, client, db):
        url = reverse("dashboard:news_key_takeaways")
        response = client.post(url)
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# AI Observatory
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAiObservatoryView:
    def test_returns_200(self, client, db):
        url = reverse("dashboard:ai_observatory")
        response = client.get(url)
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Momentum
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMomentumLeaderboardView:
    def test_returns_200(self, client, db):
        url = reverse("dashboard:momentum")
        response = client.get(url)
        assert response.status_code == 200


@pytest.mark.django_db
class TestMomentumChartData:
    def test_returns_json_200(self, client, db):
        url = reverse("dashboard:momentum_chart_data")
        response = client.get(url)
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Undismiss alert
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUndismissAlert:
    def test_post_undismisses_alert(self, client):
        alert = AlertFactory()
        AlertSeen.objects.create(user=client._user, alert=alert)
        url = reverse("dashboard:undismiss_alert", kwargs={"pk": alert.pk})
        response = client.post(url)
        assert response.status_code == 200
        assert not AlertSeen.objects.filter(user=client._user, alert=alert).exists()


# ---------------------------------------------------------------------------
# Dismissed alerts partial
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPartialDismissedAlerts:
    def test_returns_200(self, client, db):
        url = reverse("dashboard:partial_dismissed_alerts")
        response = client.get(url)
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Metric chart detail and peers
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMetricChartDetailView:
    def test_returns_200_with_data(self, client, entity_with_metrics):
        url = reverse(
            "dashboard:metric_chart_detail",
            kwargs={"entity_slug": entity_with_metrics.slug, "metric_key": "github_stars"},
        )
        response = client.get(url)
        assert response.status_code == 200

    def test_returns_200_no_data(self, client, entity):
        url = reverse(
            "dashboard:metric_chart_detail",
            kwargs={"entity_slug": entity.slug, "metric_key": "github_stars"},
        )
        response = client.get(url)
        assert response.status_code == 200

    def test_404_for_unknown_entity(self, client):
        url = reverse(
            "dashboard:metric_chart_detail",
            kwargs={"entity_slug": "no-such-entity", "metric_key": "github_stars"},
        )
        response = client.get(url)
        assert response.status_code == 404


@pytest.mark.django_db
class TestMetricChartPeersView:
    def test_returns_json_200(self, client, entity_with_metrics):
        url = reverse(
            "dashboard:metric_chart_peers",
            kwargs={"entity_slug": entity_with_metrics.slug, "metric_key": "github_stars"},
        )
        response = client.get(url)
        assert response.status_code == 200

    def test_unknown_entity_returns_empty_peers(self, client):
        url = reverse(
            "dashboard:metric_chart_peers",
            kwargs={"entity_slug": "no-such-entity", "metric_key": "github_stars"},
        )
        response = client.get(url)
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["peers"] == []


# ---------------------------------------------------------------------------
# Executive chart JSON endpoints
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestExecChartEndpoints:
    def test_vertical_bars_returns_200(self, client, entity):
        url = reverse("dashboard:partial_exec_vertical_bars")
        response = client.get(url)
        assert response.status_code == 200

    def test_radar_returns_200(self, client, entity):
        url = reverse("dashboard:partial_exec_radar")
        response = client.get(url)
        assert response.status_code == 200

    def test_severity_returns_200(self, client, db):
        url = reverse("dashboard:partial_exec_severity")
        response = client.get(url)
        assert response.status_code == 200

    def test_news_volume_returns_200(self, client, db):
        url = reverse("dashboard:partial_exec_news_volume")
        response = client.get(url)
        assert response.status_code == 200

    def test_trend_returns_200(self, client, entity):
        url = reverse("dashboard:partial_exec_trend")
        response = client.get(url)
        assert response.status_code == 200

    def test_movers_returns_200(self, client, entity):
        url = reverse("dashboard:partial_exec_movers")
        response = client.get(url)
        assert response.status_code == 200

    @freeze_time("2026-04-14 12:00:00")
    def test_movers_news_uses_cumulative_newsitem_count(self, client, entity):
        """News movers compare week-over-week NewsItem counts, not per-fetch metric values."""
        url = reverse("dashboard:partial_exec_movers")

        # Create 10 NewsItem rows in the previous week (days 8–14 ago)
        with freeze_time("2026-04-03 12:00:00"):
            prev_items = NewsItemFactory.create_batch(10)
            for item in prev_items:
                item.entities.add(entity)

        # Create 15 NewsItem rows in the current week (days 1–7 ago)
        with freeze_time("2026-04-10 12:00:00"):
            curr_items = NewsItemFactory.create_batch(15)
            for item in curr_items:
                item.entities.add(entity)

        response = client.get(url)
        assert response.status_code == 200
        data = response.json()
        movers = data["movers"]

        # Entity should appear as a mover with +50% news delta (15 vs 10)
        news_mover = next(
            (m for m in movers if m["slug"] == entity.slug and m["metric"] == "News Articles"),
            None,
        )
        assert news_mover is not None, f"Expected news mover for {entity.slug}, got {movers}"
        assert news_mover["delta_pct"] == pytest.approx(50.0, abs=0.1)

    @freeze_time("2026-04-14 12:00:00")
    def test_movers_news_not_negative_100_for_all(self, client):
        """Regression: movers must not show -100% for every entity (was a bug)."""
        url = reverse("dashboard:partial_exec_movers")
        entities = [EntityFactory(slug=f"ent-{i}") for i in range(3)]

        # Give each entity some news in both weeks so they all have valid deltas
        for ent in entities:
            with freeze_time("2026-04-03 12:00:00"):
                prev = NewsItemFactory.create_batch(5)
                for item in prev:
                    item.entities.add(ent)
            with freeze_time("2026-04-10 12:00:00"):
                curr = NewsItemFactory.create_batch(3)
                for item in curr:
                    item.entities.add(ent)

        response = client.get(url)
        data = response.json()
        movers = data["movers"]

        # No mover should have exactly -100%
        for m in movers:
            assert m["delta_pct"] != -100.0, f"{m['name']} has -100% — regression!"


# ---------------------------------------------------------------------------
# AI Observatory data endpoints
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAiObservatoryDataEndpoints:
    def test_ai_anomaly_data_returns_200(self, client, db):
        url = reverse("dashboard:ai_anomaly_data")
        response = client.get(url)
        assert response.status_code == 200

    def test_ai_ga_data_returns_200(self, client, db):
        url = reverse("dashboard:ai_ga_data")
        response = client.get(url)
        assert response.status_code == 200

    def test_ai_alert_volume_data_returns_200(self, client, db):
        url = reverse("dashboard:ai_alert_volume_data")
        response = client.get(url)
        assert response.status_code == 200

    def test_ai_threshold_history_returns_200(self, client, db):
        url = reverse("dashboard:ai_threshold_history")
        response = client.get(url)
        assert response.status_code == 200

    def test_ai_threshold_evolution_page_returns_200(self, client, db):
        url = reverse("dashboard:ai_threshold_evolution_page")
        response = client.get(url)
        assert response.status_code == 200

    def test_ai_threshold_evolution_data_returns_200(self, client, db):
        url = reverse("dashboard:ai_threshold_evolution")
        response = client.get(url)
        assert response.status_code == 200

    def test_ai_alert_volume_with_data(self, client, entity):
        AlertFactory(entity=entity)
        url = reverse("dashboard:ai_alert_volume_data")
        response = client.get(url)
        assert response.status_code == 200
        data = json.loads(response.content)
        assert "labels" in data

# dashboard/tests/test_dashboard_tags.py
"""Tests for custom template tags and filters in dashboard_tags."""

from dashboard.templatetags.dashboard_tags import (
    format_metric,
    get_item,
    metric_label,
    metric_source,
    percent_delta,
    render_markdown,
)


class TestRenderMarkdown:
    """Tests for render_markdown filter — markdown to sanitised HTML."""

    def test_empty_string(self):
        assert render_markdown("") == ""

    def test_bold_markdown(self):
        result = render_markdown("**bold**")
        assert "<strong>bold</strong>" in result

    def test_xss_script_tag_stripped(self):
        result = render_markdown('<script>alert("xss")</script>')
        assert "<script>" not in result
        assert "alert" not in result

    def test_link_preserved(self):
        result = render_markdown("[click](https://example.com)")
        assert 'href="https://example.com"' in result

    def test_none_coerced_to_string(self):
        # render_markdown casts to str(value) for non-empty inputs
        result = render_markdown("None")
        assert result  # should not crash


class TestGetItem:
    """Tests for get_item filter — dict key lookup."""

    def test_key_exists(self):
        assert get_item({"a": 1}, "a") == 1

    def test_key_missing(self):
        assert get_item({"a": 1}, "b") is None

    def test_non_dict_input(self):
        assert get_item("not-a-dict", "a") is None

    def test_none_input(self):
        assert get_item(None, "a") is None


class TestMetricLabel:
    """Tests for metric_label filter — human-readable label lookup."""

    def test_key_in_dict(self):
        labels = {"github_stars": "GitHub Stars"}
        assert metric_label("github_stars", labels) == "GitHub Stars"

    def test_key_not_in_dict(self):
        labels = {"github_stars": "GitHub Stars"}
        assert metric_label("unknown_key", labels) == "unknown_key"

    def test_non_dict_labels(self):
        assert metric_label("any_key", "not-a-dict") == "any_key"


class TestFormatMetric:
    """Tests for format_metric filter — number formatting."""

    def test_none_returns_dash(self):
        assert format_metric(None, "github_stars") == "—"

    def test_rating_shows_one_decimal(self):
        assert format_metric(4.5, "appstore_rating") == "4.5"

    def test_rating_count_shows_integer(self):
        result = format_metric(1500, "appstore_rating_count")
        assert result == "1,500"

    def test_count_shows_comma_separated(self):
        result = format_metric(10000, "github_stars")
        assert result == "10,000"

    def test_non_numeric_fallback(self):
        assert format_metric("abc", "github_stars") == "abc"


class TestMetricSource:
    """Tests for metric_source filter — data source prefix extraction."""

    def test_github_prefix(self):
        assert metric_source({"metric_key": "github_stars"}) == "github"

    def test_news_prefix(self):
        assert metric_source({"metric_key": "news_article_count"}) == "news"

    def test_compound_rule(self):
        assert metric_source({"rule_id": 42}) == "compound"

    def test_unknown_prefix(self):
        assert metric_source({"metric_key": "unknown_thing"}) == "other"

    def test_non_dict_input(self):
        assert metric_source("not-a-dict") == "other"

    def test_empty_dict(self):
        assert metric_source({}) == "other"


class TestPercentDelta:
    """Tests for percent_delta filter — percentage difference calculation."""

    def test_positive_delta(self):
        result = percent_delta(110, 100)
        assert result == "+10%"

    def test_negative_delta(self):
        result = percent_delta(90, 100)
        assert result == "-10%"

    def test_near_zero_returns_equal(self):
        result = percent_delta(100, 100)
        assert result == "="

    def test_zero_baseline_nonzero_value(self):
        result = percent_delta(10, 0)
        assert result == "∞"

    def test_both_zero(self):
        result = percent_delta(0, 0)
        assert result == "—"

    def test_none_a_returns_empty(self):
        assert percent_delta(None, 100) == ""

    def test_none_b_returns_empty(self):
        assert percent_delta(100, None) == ""

    def test_non_numeric_returns_empty(self):
        assert percent_delta("abc", 100) == ""

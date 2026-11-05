# dashboard/templatetags/dashboard_tags.py
"""Custom template tags and filters for the dashboard."""

import logging

import markdown as md
import nh3
from django import template

register = template.Library()
logger = logging.getLogger(__name__)

# Tags and attributes allowed through the HTML sanitiser.
_ALLOWED_TAGS = {
    "p",
    "br",
    "strong",
    "em",
    "b",
    "i",
    "u",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "ul",
    "ol",
    "li",
    "a",
    "code",
    "pre",
    "blockquote",
    "hr",
}
_ALLOWED_ATTRIBUTES: dict[str, set[str]] = {
    "a": {"href", "title"},
}


@register.filter
def render_markdown(value: str) -> str:
    """
    Convert LLM markdown output to sanitised HTML for display.

    The raw HTML produced by the markdown library is passed through ``nh3``
    to strip any tags or attributes not in the allowlist, preventing XSS
    from untrusted LLM output.

    Usage in template:
        {{ briefing.content|render_markdown|safe }}
    """
    if not value:
        return ""
    raw_html = md.markdown(str(value), extensions=["nl2br"])
    return nh3.clean(raw_html, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRIBUTES)


@register.filter
def get_item(dictionary: dict, key: str):
    """
    Retrieve a value from a dict by a dynamic key.

    Usage in template:
        {{ my_dict|get_item:some_variable }}
    """
    if not isinstance(dictionary, dict):
        return None
    return dictionary.get(key)


@register.filter
def metric_label(key: str, labels: dict) -> str:
    """
    Return a human-readable label for a metric key.

    Usage:
        {{ metric_key|metric_label:metric_labels }}
    """
    if isinstance(labels, dict):
        return labels.get(key, key)
    return key


@register.filter
def format_metric(value, key: str) -> str:
    """
    Format a metric value for display.

    Ratings show one decimal place; counts show as integers.
    """
    if value is None:
        return "—"
    try:
        f = float(value)
        if "rating" in key and "count" not in key:
            return f"{f:.1f}"
        return f"{int(f):,}"
    except (TypeError, ValueError):
        return str(value)


@register.filter
def metric_source(metadata: dict) -> str:
    """
    Return the data source prefix from alert metadata metric_key.

    Returns 'github', 'news', 'appstore', or 'other'.
    Used to set data-source attributes on alert cards for Alpine.js filtering.

    Usage:
        {{ alert.metadata|metric_source }}
    """
    if not isinstance(metadata, dict):
        return "other"
    # Compound alerts have a rule_id instead of a single metric_key
    if "rule_id" in metadata:
        return "compound"
    key = metadata.get("metric_key", "")
    for prefix in ("github", "news", "appstore", "twitter", "reddit"):
        if key.startswith(prefix):
            return prefix
    return "other"


@register.filter
def percent_delta(value_a, value_b) -> str:
    """
    Return the % difference of value_a relative to value_b.

    Examples: +12%, -8%, =, or '' when either value is missing.
    """
    if value_a is None or value_b is None:
        return ""
    try:
        fa, fb = float(value_a), float(value_b)
    except (TypeError, ValueError):
        return ""
    if fb == 0:
        return "—" if fa == 0 else "∞"
    pct = ((fa - fb) / abs(fb)) * 100
    if abs(pct) < 0.5:
        return "="
    sign = "+" if pct > 0 else ""
    return f"{sign}{pct:.0f}%"


@register.filter
def format_compact_number(value) -> str:
    """
    Format a number compactly for KPI cards (e.g. 12345 → '12.3K', 1234567 → '1.2M').

    Usage:
        {{ count|format_compact_number }}
    """
    if value is None:
        return "—"
    try:
        n = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if abs(n) >= 1_000:
        return f"{n / 1_000:.1f}K"
    if n == int(n):
        return f"{int(n):,}"
    return f"{n:.1f}"

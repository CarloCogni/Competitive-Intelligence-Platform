# metrics/tests/factories.py
"""factory-boy factories for metrics models."""

import factory
from django.utils import timezone

from core.tests.factories import EntityFactory
from metrics.models import (
    Alert,
    CandidateEntity,
    DeltaThreshold,
    Metric,
    MetricSnapshot,
    NewsItem,
)


class CandidateEntityFactory(factory.django.DjangoModelFactory):
    """Factory for CandidateEntity."""

    class Meta:
        model = CandidateEntity

    name = factory.Sequence(lambda n: f"Competitor {n}")
    mention_count = 1
    category = "competitor"
    relevance_score = 7
    llm_reason = "Direct competitor in Bitcoin infrastructure."
    sample_urls = factory.LazyFunction(list)


class MetricSnapshotFactory(factory.django.DjangoModelFactory):
    """Factory for MetricSnapshot."""

    class Meta:
        model = MetricSnapshot

    entity = factory.SubFactory(EntityFactory)
    source = "github"
    data = factory.LazyFunction(dict)


class MetricFactory(factory.django.DjangoModelFactory):
    """Factory for Metric."""

    class Meta:
        model = Metric

    entity = factory.SubFactory(EntityFactory)
    source = "github"
    key = "github_stars"
    value = 1000.0
    captured_at = factory.LazyFunction(timezone.now)


class AlertFactory(factory.django.DjangoModelFactory):
    """Factory for Alert."""

    class Meta:
        model = Alert

    entity = factory.SubFactory(EntityFactory)
    alert_type = Alert.AlertType.METRIC_SPIKE
    message = "Test alert message."
    metadata = factory.LazyFunction(dict)
    seen = False


class NewsItemFactory(factory.django.DjangoModelFactory):
    """Factory for NewsItem."""

    class Meta:
        model = NewsItem

    title = factory.Sequence(lambda n: f"News article {n}")
    url = factory.Sequence(lambda n: f"https://example.com/article-{n}")
    source_name = "Test Source"
    published_at = factory.LazyFunction(timezone.now)
    content = None
    content_scraped_at = None


class DeltaThresholdFactory(factory.django.DjangoModelFactory):
    """Factory for DeltaThreshold."""

    class Meta:
        model = DeltaThreshold
        django_get_or_create = ("metric_key",)

    metric_key = factory.Sequence(lambda n: f"test_metric_{n}")
    threshold_pct = 0.20
    lookback_days = 7
    use_dynamic_threshold = False

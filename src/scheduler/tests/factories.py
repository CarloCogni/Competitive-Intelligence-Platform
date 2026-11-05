# scheduler/tests/factories.py
"""Factory-boy factories for scheduler models."""

import factory
from django.utils import timezone

from scheduler.models import BatchRun, FetcherConfig


class FetcherConfigFactory(factory.django.DjangoModelFactory):
    """Factory for FetcherConfig."""

    class Meta:
        model = FetcherConfig
        django_get_or_create = ("source",)

    source = "github"
    interval_hours = 24
    enabled = True


class BatchRunFactory(factory.django.DjangoModelFactory):
    """Factory for BatchRun."""

    class Meta:
        model = BatchRun

    job_id = factory.Sequence(lambda n: f"job_{n}")
    triggered_at = factory.LazyFunction(timezone.now)
    started_at = factory.LazyFunction(timezone.now)
    status = BatchRun.Status.RUNNING

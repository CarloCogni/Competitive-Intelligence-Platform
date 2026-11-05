# briefings/tests/factories.py
"""factory-boy factories for briefings models."""

import factory
from django.utils import timezone

from briefings.models import WeeklyBriefing


class WeeklyBriefingFactory(factory.django.DjangoModelFactory):
    """Factory for WeeklyBriefing."""

    class Meta:
        model = WeeklyBriefing

    period_start = factory.LazyFunction(lambda: timezone.now() - timezone.timedelta(days=7))
    period_end = factory.LazyFunction(timezone.now)
    content = "**Executive Summary**\nTest briefing content."
    model_used = "ollama:gemma4:e4b"
    status = WeeklyBriefing.Status.READY
    generated_at = factory.LazyFunction(timezone.now)
    error_message = ""
    entity_count = 5
    alert_count = 3
    news_count = 4

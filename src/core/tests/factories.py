# core/tests/factories.py
"""factory-boy factories for core models."""

import factory

from core.models import Entity, Vertical


class VerticalFactory(factory.django.DjangoModelFactory):
    """Factory for Vertical."""

    class Meta:
        model = Vertical
        django_get_or_create = ("slug",)

    name = factory.Sequence(lambda n: f"Vertical {n}")
    slug = factory.Sequence(lambda n: f"vertical-{n}")


class EntityFactory(factory.django.DjangoModelFactory):
    """Factory for Entity."""

    class Meta:
        model = Entity
        django_get_or_create = ("slug",)

    name = factory.Sequence(lambda n: f"Entity {n}")
    slug = factory.Sequence(lambda n: f"entity-{n}")
    vertical = factory.SubFactory(VerticalFactory)
    is_primary = False
    github_org = ""
    github_repo = ""
    app_store_id = ""
    news_keywords = factory.LazyFunction(list)
    twitter_handle = ""
    reddit_keywords = factory.LazyFunction(list)


class PrimaryEntityFactory(EntityFactory):
    """Factory for a Blockstream (primary) entity with GitHub config."""

    is_primary = True
    github_org = "Blockstream"
    github_repo = "green_qt"
    news_keywords = factory.LazyFunction(lambda: ["Blockstream", "Liquid Network"])

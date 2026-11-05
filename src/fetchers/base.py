# fetchers/base.py
"""
BaseFetcher — abstract base class for all data fetchers.

Lifecycle (called by scheduler job or directly in tests/shell):
    fetcher = GithubFetcher(entity)
    fetcher.run()   →  fetch()  →  store(data)

Each fetcher subclass must implement:
    - source_name: str class attribute (used as Metric.source prefix)
    - fetch() -> dict   hit the external API, return raw payload
    - extract_metrics(data) -> dict[str, float | None]  map payload to flat metrics
"""

import logging
from abc import ABC, abstractmethod
from typing import Any

from core.models import Entity
from metrics.models import Metric, MetricSnapshot

logger = logging.getLogger(__name__)


class BaseFetcher(ABC):
    """Abstract fetcher. Subclass and implement fetch() + extract_metrics()."""

    # Must be overridden: used as MetricSnapshot.source and Metric.source prefix
    source_name: str = ""

    def __init__(self, entity: Entity) -> None:
        self.entity = entity
        self._logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        """
        Fetch data and persist it. Catches all exceptions so a single
        fetcher failure never crashes the scheduler.
        """
        try:
            data = self.fetch()
            if data:
                self.store(data)
        except Exception:
            self._logger.exception(
                "Fetcher %s failed for entity '%s'",
                self.__class__.__name__,
                self.entity.name,
            )

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abstractmethod
    def fetch(self) -> dict[str, Any]:
        """
        Hit the external API and return a raw payload dict.
        Return an empty dict if the entity has no relevant config.
        """

    @abstractmethod
    def extract_metrics(self, data: dict[str, Any]) -> dict[str, float | None]:
        """
        Map raw payload to flat key/value metrics.
        Keys must be prefixed with source_name (e.g. 'github_stars').
        """

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def store(self, data: dict[str, Any]) -> None:
        """
        Persist a MetricSnapshot (raw JSON) and flat Metric rows.
        Called automatically by run() after a successful fetch().
        """
        # Raw snapshot
        MetricSnapshot.objects.create(
            entity=self.entity,
            source=self.source_name,
            data=data,
        )

        # Flat metric rows
        metrics = self.extract_metrics(data)
        metric_objects = [
            Metric(
                entity=self.entity,
                source=self.source_name,
                key=key,
                value=value,
            )
            for key, value in metrics.items()
        ]
        Metric.objects.bulk_create(metric_objects)

        self._logger.info(
            "Stored %d metric(s) for '%s' from %s.",
            len(metric_objects),
            self.entity.name,
            self.source_name,
        )

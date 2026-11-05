# core/management/commands/seed.py
"""
Management command: seed

Loads entities.json into the database (idempotent).
Running twice produces the same DB state — no duplicates.

Usage:
    python manage.py seed
    python manage.py seed --file /path/to/custom-entities.json
"""

import json
import logging
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.models import Entity, Vertical

logger = logging.getLogger(__name__)

# Default entities file sits alongside manage.py (src/entities.json)
DEFAULT_ENTITIES_FILE = Path(settings.BASE_DIR) / "entities.json"


class Command(BaseCommand):
    """Load verticals and entities from entities.json into the database."""

    help = "Seed the database from entities.json (idempotent)."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--file",
            type=Path,
            default=DEFAULT_ENTITIES_FILE,
            help="Path to a custom entities JSON file.",
        )

    def handle(self, *args, **options) -> None:
        """Execute the seed command."""
        entities_file: Path = options["file"]

        if not entities_file.exists():
            raise CommandError(f"Entities file not found: {entities_file}")

        data = json.loads(entities_file.read_text(encoding="utf-8"))

        verticals_created = self._seed_verticals(data.get("verticals", []))
        entities_created = self._seed_entities(data.get("entities", []))

        self.stdout.write(
            self.style.SUCCESS(
                f"Seed complete — "
                f"{verticals_created} vertical(s) created, "
                f"{entities_created} entity/entities created."
            )
        )

    def _seed_verticals(self, verticals_data: list[dict]) -> int:
        """Create Vertical rows; return count of newly created rows."""
        created = 0
        for v in verticals_data:
            _, was_created = Vertical.objects.get_or_create(
                slug=v["slug"],
                defaults={"name": v["name"]},
            )
            if was_created:
                created += 1
                logger.info("Created vertical: %s", v["name"])
        return created

    def _seed_entities(self, entities_data: list[dict]) -> int:
        """Create Entity rows; return count of newly created rows."""
        created = 0
        for e in entities_data:
            # Resolve vertical by slug (may be None if missing)
            vertical = None
            if e.get("vertical"):
                try:
                    vertical = Vertical.objects.get(slug=e["vertical"])
                except Vertical.DoesNotExist:
                    logger.warning(
                        "Vertical '%s' not found for entity '%s' — skipping vertical link.",
                        e["vertical"],
                        e["name"],
                    )

            _, was_created = Entity.objects.update_or_create(
                slug=e["slug"],
                defaults={
                    "name": e["name"],
                    "vertical": vertical,
                    "is_primary": e.get("is_primary", False),
                    "github_org": e.get("github_org", ""),
                    "github_repo": e.get("github_repo", ""),
                    "app_store_id": e.get("app_store_id", ""),
                    "news_keywords": e.get("news_keywords", []),
                    "twitter_handle": e.get("twitter_handle", ""),
                    "reddit_keywords": e.get("reddit_keywords", []),
                    "play_store_id": e.get("play_store_id", ""),
                },
            )
            if was_created:
                created += 1
                logger.info("Created entity: %s", e["name"])
        return created

# metrics/management/commands/backfill_release_urls.py
"""
Management command: backfill_release_urls

Populates missing release_url metadata on existing NEW_RELEASE alerts by
constructing the URL from entity.github_org + entity.github_repo + release_tag.

Usage:
    python manage.py backfill_release_urls            # update alerts in-place
    python manage.py backfill_release_urls --dry-run   # preview without saving
"""

import logging

from django.core.management.base import BaseCommand

from metrics.models import Alert

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Backfill release_url on NEW_RELEASE alerts that are missing it."""

    help = "Backfill missing release_url metadata on NEW_RELEASE alerts."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be updated without saving.",
        )

    def handle(self, *args, **options) -> None:
        """Execute the backfill_release_urls command."""
        dry_run: bool = options["dry_run"]

        alerts = Alert.objects.filter(alert_type=Alert.AlertType.NEW_RELEASE).select_related(
            "entity"
        )

        updated = 0
        skipped = 0

        for alert in alerts:
            existing_url = alert.metadata.get("release_url", "")
            if existing_url:
                # Already has a URL — nothing to do
                continue

            tag = alert.metadata.get("release_tag", "")
            entity = alert.entity
            if not tag or not entity.github_org or not entity.github_repo:
                skipped += 1
                if dry_run:
                    self.stdout.write(
                        f"  SKIP alert pk={alert.pk} entity='{entity.name}' "
                        f"(tag='{tag}', org='{entity.github_org}', repo='{entity.github_repo}')"
                    )
                continue

            release_url = (
                f"https://github.com/{entity.github_org}/{entity.github_repo}/releases/tag/{tag}"
            )

            if dry_run:
                self.stdout.write(
                    f"  WOULD UPDATE alert pk={alert.pk} entity='{entity.name}' "
                    f"tag='{tag}' → {release_url}"
                )
            else:
                alert.metadata["release_url"] = release_url
                alert.save(update_fields=["metadata"])

            updated += 1

        action = "Would update" if dry_run else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{action} {updated} alert(s), skipped {skipped}."))

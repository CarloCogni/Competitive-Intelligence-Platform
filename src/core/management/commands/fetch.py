# core/management/commands/fetch.py
"""
Management command: fetch

Triggers data fetchers immediately, bypassing the scheduler's interval.
Useful for bootstrapping a fresh database or forcing a refresh on demand.

Usage:
    python manage.py fetch                      # all sources, all entities
    python manage.py fetch --source github      # GitHub only
    python manage.py fetch --source news        # News only
    python manage.py fetch --source appstore    # App Store only
    python manage.py fetch --source changelog   # Changelog only
    python manage.py fetch --source playstore   # Play Store only
    python manage.py fetch --source twitter     # Twitter only
    python manage.py fetch --source reddit      # Reddit only
    python manage.py fetch --entity jade        # all sources, one entity
    python manage.py fetch --source github --entity jade
"""

import logging
from argparse import ArgumentParser

from django.core.management.base import BaseCommand, CommandError

from core.models import Entity

logger = logging.getLogger(__name__)

SOURCES: tuple[str, ...] = (
    "github",
    "news",
    "appstore",
    "changelog",
    "playstore",
    "twitter",
    "reddit",
)


class Command(BaseCommand):
    """Run one or all data fetchers immediately, for all or one entity."""

    help = "Run data fetchers immediately (bypasses scheduler wait)."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "--source",
            choices=[*SOURCES, "all"],
            default="all",
            help="Which fetcher to run (default: all).",
        )
        parser.add_argument(
            "--entity",
            type=str,
            default=None,
            metavar="SLUG",
            help="Slug of a single entity to fetch. Omit to fetch all configured entities.",
        )

    def handle(self, *args, **options) -> None:
        """Execute the fetch command."""
        source: str = options["source"]
        slug: str | None = options["entity"]

        sources_to_run = SOURCES if source == "all" else (source,)

        if slug:
            self._fetch_one_entity(slug, sources_to_run)
        else:
            self._fetch_all_entities(sources_to_run)

    # ------------------------------------------------------------------
    # Source → display name / fetcher class path mapping
    # ------------------------------------------------------------------

    _SOURCE_META: dict[str, tuple[str, str]] = {
        "github": ("GitHub", "fetchers.github.GithubFetcher"),
        "news": ("News", "fetchers.news.NewsFetcher"),
        "appstore": ("App Store", "fetchers.appstore.AppStoreFetcher"),
        "changelog": ("Changelog", "fetchers.changelog.ChangelogFetcher"),
        "playstore": ("Play Store", "fetchers.playstore.PlayStoreFetcher"),
        "twitter": ("Twitter", "fetchers.twitter.TwitterFetcher"),
        "reddit": ("Reddit", "fetchers.reddit.RedditFetcher"),
    }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _fetch_one_entity(self, slug: str, sources: tuple[str, ...]) -> None:
        """Run fetchers for a single entity identified by slug."""
        from metrics.delta import run_delta_detection

        try:
            entity = Entity.objects.get(slug=slug)
        except Entity.DoesNotExist:
            raise CommandError(f"No entity with slug '{slug}'. Run 'manage.py seed' first.")

        self.stdout.write(f"Fetching data for entity: {entity.name}")

        for source in sources:
            display_name, _ = self._SOURCE_META[source]
            self._run(
                f"{display_name} → {entity.name}",
                self._run_single_fetcher,
                source,
                entity,
            )
            run_delta_detection(source)

        self.stdout.write(self.style.SUCCESS("Done."))

    def _fetch_all_entities(self, sources: tuple[str, ...]) -> None:
        """Run bulk fetcher job functions (same logic as the scheduler)."""
        import scheduler.jobs as jobs

        job_map: dict[str, object] = {
            "github": jobs.run_github_fetcher,
            "news": jobs.run_news_fetcher,
            "appstore": jobs.run_appstore_fetcher,
            "changelog": jobs.run_changelog_fetcher,
            "playstore": jobs.run_playstore_fetcher,
            "twitter": jobs.run_twitter_fetcher,
            "reddit": jobs.run_reddit_fetcher,
        }

        for source in sources:
            display_name, _ = self._SOURCE_META[source]
            self._run(f"{display_name} (all entities)", job_map[source])

        self.stdout.write(self.style.SUCCESS("Done."))

    def _run(self, label: str, fn: object, *args: object) -> None:
        """Call fn(*args), write progress, catch and log any exception."""
        self.stdout.write(f"  Running: {label} …")
        try:
            fn(*args)
            self.stdout.write(self.style.SUCCESS(f"  [OK] {label}"))
        except Exception as exc:
            logger.exception("Fetcher failed: %s", label)
            self.stderr.write(self.style.ERROR(f"  [FAIL] {label}: {exc}"))

    @staticmethod
    def _run_single_fetcher(source: str, entity: Entity) -> None:
        """Instantiate and run the fetcher for a given source and entity."""
        import importlib

        _, class_path = Command._SOURCE_META[source]
        module_path, class_name = class_path.rsplit(".", 1)
        module = importlib.import_module(module_path)
        fetcher_class = getattr(module, class_name)
        fetcher_class(entity).run()

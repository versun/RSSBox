from datetime import timedelta
from importlib import import_module
from unittest.mock import patch

from django.apps import apps
from django.test import TestCase
from django.utils import timezone

from core.models import Digest, Entry, Feed, OpenAIAgent, Tag
from core.tasks.generate_digests import DigestGenerator


class DigestSummaryFailureTests(TestCase):
    def setUp(self):
        self.agent = OpenAIAgent.objects.create(
            name="Digest summarizer", api_key="unused", max_tokens=4096
        )
        self.feed = Feed.objects.create(
            name="News feed",
            slug="news-feed",
            feed_url="https://example.com/news.xml",
            summarizer=self.agent,
        )
        self.tag = Tag.objects.create(name="Daily news")
        self.feed.tags.add(self.tag)
        self.entry = Entry.objects.create(
            feed=self.feed,
            link="https://example.com/article",
            original_title="Important article",
            original_content="Article body",
            pubdate=timezone.now() - timedelta(hours=1),
        )
        self.digest = Digest.objects.create(
            name="Daily digest",
            summarizer=self.agent,
            target_language="English",
            days_range=1,
        )
        self.digest.tags.add(self.tag)

    def test_summary_failure_is_not_saved_and_digest_reports_failure(self):
        with patch(
            "core.tasks.summarize_feeds._summarize_entry",
            side_effect=RuntimeError("provider down"),
        ):
            result = DigestGenerator(self.digest).generate()

        self.entry.refresh_from_db()
        self.digest.refresh_from_db()
        self.assertIsNone(self.entry.ai_summary)
        self.assertFalse(result["success"])
        self.assertFalse(self.digest.status)
        self.assertEqual(self.digest.get_digest_feed().entries.count(), 0)

    def test_no_articles_sets_status_false(self):
        self.digest.tags.clear()
        self.digest.status = True
        self.digest.save(update_fields=["status"])

        result = DigestGenerator(self.digest).generate()

        self.digest.refresh_from_db()
        self.assertFalse(result["success"])
        self.assertFalse(self.digest.status)
        self.assertIn("No articles", self.digest.log or "")

    def test_migration_clears_legacy_failure_markers(self):
        migration = import_module("core.migrations.0039_clear_legacy_summary_failures")
        self.entry.ai_summary = "[Summary failed: provider down]"
        self.entry.save(update_fields=["ai_summary"])
        kept = Entry.objects.create(
            feed=self.feed,
            link="https://example.com/kept",
            original_title="Kept",
            ai_summary="A real summary",
            pubdate=timezone.now(),
        )

        migration.clear_legacy_summary_failures(apps, None)

        self.entry.refresh_from_db()
        kept.refresh_from_db()
        self.assertIsNone(self.entry.ai_summary)
        self.assertEqual(kept.ai_summary, "A real summary")

    def test_partial_failure_does_not_report_success(self):
        second = Entry.objects.create(
            feed=self.feed,
            link="https://example.com/second",
            original_title="Second article",
            original_content="Second body",
            pubdate=timezone.now() - timedelta(minutes=15),
        )

        def summarize(entry, **kwargs):
            if entry.pk == second.pk:
                raise RuntimeError("provider down")
            return "First summary", 3

        with patch(
            "core.tasks.summarize_feeds._summarize_entry", side_effect=summarize
        ):
            result = DigestGenerator(self.digest).generate()

        second.refresh_from_db()
        self.digest.refresh_from_db()
        self.assertIsNone(second.ai_summary)
        self.assertFalse(result["success"])
        self.assertFalse(self.digest.status)
        self.assertEqual(self.digest.get_digest_feed().entries.count(), 0)
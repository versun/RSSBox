from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Digest, Entry, Feed, OpenAIAgent

WRITE_PREFIXES = ("INSERT", "UPDATE", "DELETE", "REPLACE")


class DigestGetIsReadOnlyTests(TestCase):
    """The public digest page and digest feed must not write on GET."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        agent = OpenAIAgent.objects.create(name="Agent", api_key="unused")
        self.digest = Digest.objects.create(
            name="Daily digest",
            description="About things",
            summarizer=agent,
            target_language="English",
        )

    def _get(self, path):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(path)
        writes = [
            q["sql"]
            for q in queries.captured_queries
            if q["sql"].lstrip().upper().startswith(WRITE_PREFIXES)
        ]
        return response, writes

    def _generated_digest(self):
        feed = self.digest.get_digest_feed()
        self.entry = Entry.objects.create(
            feed=feed,
            link=f"https://example.com/rss/digest/view/{self.digest.slug}",
            original_title="Daily digest | today",
            ai_summary="Digest body",
            pubdate=timezone.now(),
        )
        return feed

    def test_page_before_first_digest_is_404_and_creates_no_feed(self):
        response, writes = self._get(f"/rss/digest/view/{self.digest.slug}")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(writes, [])
        self.assertIsNone(self.digest.find_digest_feed())

    def test_feed_before_first_digest_is_404_and_creates_no_feed(self):
        response, writes = self._get(f"/rss/digest/{self.digest.slug}")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(writes, [])
        self.assertIsNone(self.digest.find_digest_feed())

    def test_page_does_not_update_the_feed_row(self):
        feed = self._generated_digest()
        # A rename that has not reached the feed row yet: before, any GET
        # copied it over (an UPDATE on a public, unauthenticated request).
        Digest.objects.filter(pk=self.digest.pk).update(name="Renamed")

        response, writes = self._get(f"/rss/digest/view/{self.digest.slug}")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Digest body")
        self.assertEqual(writes, [])
        feed.refresh_from_db()
        self.assertEqual(feed.name, "Daily digest")

    def test_entry_page_before_first_digest_is_404_and_creates_no_feed(self):
        # An entry id with no digest feed yet (a stale or guessed permalink).
        response, writes = self._get(f"/rss/digest/view/{self.digest.slug}/1")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(writes, [])
        self.assertIsNone(self.digest.find_digest_feed())

    def test_entry_page_does_not_update_the_feed_row(self):
        feed = self._generated_digest()
        Digest.objects.filter(pk=self.digest.pk).update(name="Renamed")

        response, writes = self._get(
            f"/rss/digest/view/{self.digest.slug}/{self.entry.pk}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Digest body")
        self.assertEqual(writes, [])
        feed.refresh_from_db()
        self.assertEqual(feed.name, "Daily digest")

    def test_feed_does_not_update_the_feed_row(self):
        feed = self._generated_digest()
        Digest.objects.filter(pk=self.digest.pk).update(name="Renamed")

        response, writes = self._get(f"/rss/digest/{self.digest.slug}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(writes, [])
        feed.refresh_from_db()
        self.assertEqual(feed.name, "Daily digest")

    def test_find_digest_feed_prefers_the_target_language_row(self):
        # Legacy unlinked duplicate rows (before Digest.feed FK is populated)
        # fall back to matching feed_url and prefer target_language.
        feed_url = self.digest._digest_feed_url()
        first = Feed.objects.create(
            name="Daily digest",
            feed_url=feed_url,
            target_language=self.digest.target_language,
        )
        second = Feed.objects.create(
            name="Daily digest",
            feed_url=feed_url,
            target_language="German",
        )
        self.assertEqual(self.digest.find_digest_feed(), first)
        self.digest.target_language = "German"
        self.assertEqual(self.digest.find_digest_feed(), second)

    def test_sync_digest_feed_updates_but_never_creates(self):
        self.assertIsNone(self.digest.sync_digest_feed())
        self.assertIsNone(self.digest.find_digest_feed())

        feed = self.digest.get_digest_feed()
        self.digest.name = "Renamed"
        self.digest.description = "New description"
        self.assertEqual(self.digest.sync_digest_feed(), feed)
        feed.refresh_from_db()
        self.assertEqual(feed.name, "Renamed")
        self.assertEqual(feed.subtitle, "New description")

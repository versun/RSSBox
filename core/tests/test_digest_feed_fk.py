from importlib import import_module
from urllib.parse import urlsplit

from django.apps import apps
from django.contrib.admin.sites import AdminSite
from django.core.cache import cache
from django.db import transaction
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone
from django.utils.http import http_date

from core.admin.feed_admin import FeedAdmin
from core.models import Digest, Entry, Feed, OpenAIAgent
from core.models.filter import bump_cache_version
from core.views import _get_digest_etag, _get_digest_modified


class DigestFeedFKTests(TestCase):
    def setUp(self):
        self.agent = OpenAIAgent.objects.create(
            name="Summarizer", api_key="unused", valid=True
        )
        self.digest = Digest.objects.create(
            name="Tech Daily",
            slug="tech-daily",
            description="Initial description",
            summarizer=self.agent,
            target_language="English",
        )

    def _create_entry(self, feed, body="Digest body"):
        return Entry.objects.create(
            feed=feed,
            link=f"http://localhost:8000/rss/digest/view/{self.digest.slug}",
            original_title="Issue 1",
            ai_summary=body,
            pubdate=timezone.now(),
        )

    def test_get_digest_feed_links_explicit_fk(self):
        self.assertIsNone(self.digest.feed_id)
        feed = self.digest.get_digest_feed()
        self.digest.refresh_from_db()
        self.assertEqual(self.digest.feed_id, feed.pk)
        self.assertEqual(self.digest.find_digest_feed(), feed)

    def test_renaming_slug_preserves_feed_and_entries(self):
        feed = self.digest.get_digest_feed()
        entry = self._create_entry(feed, body="Preserved after slug rename")

        self.digest.slug = "tech-weekly"
        self.digest.save()

        self.digest.refresh_from_db()
        feed.refresh_from_db()
        entry.refresh_from_db()
        self.assertEqual(self.digest.find_digest_feed(), feed)
        self.assertTrue(feed.feed_url.endswith("/rss/digest/tech-weekly"))
        self.assertEqual(feed.link, feed.feed_url)
        self.assertEqual(list(feed.entries.values_list("pk", flat=True)), [entry.pk])
        self.assertTrue(entry.link.endswith(f"/rss/digest/view/tech-weekly/{entry.pk}"))

        resp = self.client.get(urlsplit(entry.link).path)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Preserved after slug rename")

    def test_changing_site_url_preserves_feed_and_public_views(self):
        feed = self.digest.get_digest_feed()
        entry = self._create_entry(feed, body="Preserved after SITE_URL change")

        with override_settings(SITE_URL="https://rssbox.new-domain.example"):
            reloaded = Digest.objects.select_related("feed").get(pk=self.digest.pk)
            self.assertEqual(reloaded.find_digest_feed(), feed)

            page_resp = self.client.get(f"/rss/digest/view/{self.digest.slug}/{entry.pk}")
            self.assertEqual(page_resp.status_code, 200)
            self.assertContains(page_resp, "Preserved after SITE_URL change")

            feed_resp = self.client.get(f"/rss/digest/{self.digest.slug}")
            self.assertEqual(feed_resp.status_code, 200)

    def test_orm_save_syncs_linked_feed_metadata(self):
        feed = self.digest.get_digest_feed()
        self.digest.name = "Updated via ORM"
        self.digest.description = "New summary description"
        self.digest.target_language = "Japanese"
        self.digest.save()

        feed.refresh_from_db()
        self.assertEqual(feed.name, "Updated via ORM")
        self.assertEqual(feed.subtitle, "New summary description")
        self.assertEqual(feed.target_language, "Japanese")
        self.assertEqual(feed.language, "Japanese")

    def test_deleting_digest_after_site_url_change_deletes_linked_feed(self):
        feed = self.digest.get_digest_feed()
        self._create_entry(feed)

        with override_settings(SITE_URL="https://moved.example"):
            self.digest.delete()

        self.assertFalse(Feed.objects.filter(pk=feed.pk).exists())
        self.assertEqual(Entry.objects.filter(feed_id=feed.pk).count(), 0)

    def test_feed_admin_excludes_linked_digest_feed_even_without_author_marker(self):
        feed = self.digest.get_digest_feed()
        Feed.objects.filter(pk=feed.pk).update(author="Custom Author")
        regular_feed = Feed.objects.create(
            name="Regular Feed", feed_url="https://example.com/rss.xml"
        )

        admin_instance = FeedAdmin(Feed, AdminSite())
        request = RequestFactory().get("/admin/core/feed/")
        qs = admin_instance.get_queryset(request)

        self.assertNotIn(feed, qs)
        self.assertIn(regular_feed, qs)

    def test_sync_reattaches_when_in_memory_feed_id_is_stale(self):
        old_feed = self.digest.get_digest_feed()
        # Keep self.digest with stale in-memory feed_id while DB row is deleted.
        stale_digest = Digest.objects.get(pk=self.digest.pk)
        Feed.objects.filter(pk=old_feed.pk).delete()
        replacement = Feed.objects.create(
            name="Replacement",
            feed_url=stale_digest._digest_feed_url(),
            author="RSSBox Digest",
            target_language=stale_digest.target_language,
        )

        synced = stale_digest.sync_digest_feed()
        stale_digest.refresh_from_db()
        self.assertEqual(synced.pk, replacement.pk)
        self.assertEqual(stale_digest.feed_id, replacement.pk)

    def test_attach_handles_onetoone_integrity_error_inside_atomic_block(self):
        feed = self.digest.get_digest_feed()
        other = Digest.objects.create(
            name="Other",
            slug="other-digest",
            summarizer=self.agent,
            target_language="English",
        )

        with transaction.atomic():
            attached = other._attach_digest_feed(feed)
            # Outer transaction must remain usable after savepoint rollback.
            self.assertEqual(attached.pk, feed.pk)
            self.assertTrue(Digest.objects.filter(pk=other.pk).exists())

        other.refresh_from_db()
        self.assertIsNone(other.feed_id)

    def test_ungenerated_digest_returns_404_not_304_after_cache_version_bump(self):
        cache.clear()
        try:
            self.assertIsNone(self.digest.last_generated)
            bump_cache_version("digest", self.digest.pk)

            request = RequestFactory().get(f"/rss/digest/{self.digest.slug}")
            self.assertIsNone(_get_digest_etag(request, self.digest.slug))
            self.assertIsNone(_get_digest_modified(request, self.digest.slug))

            resp = self.client.get(
                f"/rss/digest/{self.digest.slug}",
                HTTP_IF_MODIFIED_SINCE=http_date(timezone.now().timestamp()),
            )
            self.assertEqual(resp.status_code, 404)
        finally:
            cache.clear()


class DigestFeedBackfillMigrationTests(TestCase):
    def setUp(self):
        self.agent = OpenAIAgent.objects.create(
            name="Summarizer", api_key="unused", valid=True
        )
        self.migration = import_module("core.migrations.0048_digest_feed")

    def test_backfill_links_exact_site_url_and_prefers_target_language(self):
        digest = Digest.objects.create(
            name="Daily",
            slug="daily-exact",
            summarizer=self.agent,
            target_language="Japanese",
        )
        url = f"http://localhost:8000/rss/digest/{digest.slug}"
        wrong_lang = Feed.objects.create(
            name="Daily", feed_url=url, target_language="English"
        )
        right_lang = Feed.objects.create(
            name="Daily", feed_url=url, target_language="Japanese"
        )

        with override_settings(SITE_URL="http://localhost:8000"):
            self.migration.backfill_digest_feeds(apps, None)

        digest.refresh_from_db()
        self.assertEqual(digest.feed_id, right_lang.pk)
        self.assertNotEqual(digest.feed_id, wrong_lang.pk)

    def test_backfill_falls_back_to_unfetched_internal_digest_feed_when_site_url_changed(
        self,
    ):
        digest = Digest.objects.create(
            name="Moved",
            slug="moved-digest",
            summarizer=self.agent,
            target_language="English",
        )
        internal_feed = Feed.objects.create(
            name="Moved",
            feed_url="https://old-domain.example/rss/digest/moved-digest",
            author="RSSBox Digest",
            target_language="English",
        )

        with override_settings(SITE_URL="https://new-domain.example"):
            self.migration.backfill_digest_feeds(apps, None)

        digest.refresh_from_db()
        self.assertEqual(digest.feed_id, internal_feed.pk)

    def test_backfill_never_links_external_fetched_feed_with_same_slug_suffix(self):
        digest = Digest.objects.create(
            name="Daily",
            slug="shared-slug",
            summarizer=self.agent,
            target_language="English",
        )
        # External subscription to another RSSBox instance's digest feed.
        Feed.objects.create(
            name="Friend Digest",
            feed_url="https://friend.example/rss/digest/shared-slug",
            author="RSSBox Digest",
            fetch_status=True,
            last_fetch=timezone.now(),
            target_language="English",
        )

        with override_settings(SITE_URL="https://my-site.example"):
            self.migration.backfill_digest_feeds(apps, None)

        digest.refresh_from_db()
        self.assertIsNone(digest.feed_id)

    def test_backfill_links_internal_feed_touched_by_failed_feed_updater(self):
        digest = Digest.objects.create(
            name="Failed Fetch",
            slug="failed-fetch-digest",
            summarizer=self.agent,
            target_language="English",
        )
        internal_feed = Feed.objects.create(
            name="Failed Fetch",
            feed_url="https://old-domain.example/rss/digest/failed-fetch-digest",
            author="RSSBox Digest",
            fetch_status=False,
            last_fetch=None,
            target_language="English",
        )

        with override_settings(SITE_URL="https://new-domain.example"):
            self.migration.backfill_digest_feeds(apps, None)

        digest.refresh_from_db()
        self.assertEqual(digest.feed_id, internal_feed.pk)

    def test_backfill_links_self_fetched_internal_feed_with_digest_entry(self):
        digest = Digest.objects.create(
            name="Self Fetched",
            slug="self-fetched-digest",
            summarizer=self.agent,
            target_language="English",
        )
        internal_feed = Feed.objects.create(
            name="Self Fetched",
            feed_url="https://old-domain.example/rss/digest/self-fetched-digest",
            author="RSSBox Digest",
            fetch_status=True,
            last_fetch=timezone.now(),
            target_language="English",
        )
        Entry.objects.create(
            feed=internal_feed,
            link="https://old-domain.example/rss/digest/view/self-fetched-digest/1",
            guid=f"digest:{digest.pk}:1700000000",
            original_title="Self Fetched | 2026-01-01",
            original_content=None,
            ai_summary="Generated digest markdown",
            pubdate=timezone.now(),
        )

        with override_settings(SITE_URL="https://new-domain.example"):
            self.migration.backfill_digest_feeds(apps, None)

        digest.refresh_from_db()
        self.assertEqual(digest.feed_id, internal_feed.pk)

"""Regression tests for the P3 architecture-review fixes.

Covers: cache invalidation versions, single-flight generation, per-feed
update locking, entry re-fetch refresh, XML 404 on empty feeds, cleanup
ordering by pubdate, and the Entry pubdate index.
"""

import fcntl
import threading
from datetime import timedelta
from unittest.mock import Mock, patch

from django.core.cache import cache
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from core.cache import cache_rss
from core.management.commands import feed_updater as cmd
from core.management.commands.cleanup_entries import cleanup_feed_entries
from core.models import Entry, Feed, Filter, FilterResult, Tag
from core.models.filter import bump_cache_version, cache_version


class CacheVersionTests(TestCase):
    """bump_cache_version orphans rendered caches without prefix deletes."""

    def setUp(self):
        cache.clear()
        self.feed = Feed.objects.create(
            name="F", feed_url="https://example.com/f.xml", slug="cv-feed"
        )

    def tearDown(self):
        cache.clear()

    @patch("core.cache.generate_atom_feed")
    def test_bump_orphans_cached_feed(self, mock_gen):
        mock_gen.return_value = "<feed>v0</feed>"
        first = cache_rss(self.feed.slug, "t", "xml")
        self.assertEqual(first, "<feed>v0</feed>")
        self.assertEqual(mock_gen.call_count, 1)

        # Same key still served from cache.
        self.assertEqual(cache_rss(self.feed.slug, "t", "xml"), "<feed>v0</feed>")
        self.assertEqual(mock_gen.call_count, 1)

        mock_gen.return_value = "<feed>v1</feed>"
        bump_cache_version("rss", self.feed.pk)
        self.assertEqual(cache_rss(self.feed.slug, "t", "xml"), "<feed>v1</feed>")
        self.assertEqual(mock_gen.call_count, 2)

    def test_clear_filter_results_bumps_feed_and_tag_versions(self):
        tag = Tag.objects.create(name="t1")
        filt = Filter.objects.create(name="F1")
        self.feed.filters.add(filt)
        self.feed.tags.add(tag)
        tag.filters.add(filt)
        rss_before = cache_version("rss", self.feed.pk)
        tag_before = cache_version("tag", tag.pk)

        filt.clear_ai_filter_cache_results()

        self.assertGreater(cache_version("rss", self.feed.pk), rss_before)
        self.assertGreater(cache_version("tag", tag.pk), tag_before)


class SingleFlightTests(TransactionTestCase):
    """Concurrent cache misses trigger one generation, not one per thread.

    TransactionTestCase because the worker threads need their own DB
    connections; TestCase wraps everything in one transaction the other
    threads cannot see.
    """

    def setUp(self):
        cache.clear()
        self.feed = Feed.objects.create(
            name="F", feed_url="https://example.com/sf.xml", slug="sf-feed"
        )

    def tearDown(self):
        cache.clear()

    def test_concurrent_misses_generate_once(self):
        calls = []
        gate = threading.Event()

        def fake_generate(feed, feed_type):
            calls.append(1)
            gate.wait(timeout=5)  # hold the first worker so others queue up
            return "<feed>x</feed>"

        results = []
        errors = []

        def run():
            try:
                results.append(cache_rss(self.feed.slug, "t", "xml"))
            except Exception as exc:  # pragma: no cover - reported below
                errors.append(exc)

        with patch("core.cache.generate_atom_feed", side_effect=fake_generate):
            t1 = threading.Thread(target=run)
            t1.start()
            # Let t1 enter the lock first.
            threading.Event().wait(0.05)
            t2 = threading.Thread(target=run)
            t2.start()
            threading.Event().wait(0.05)
            gate.set()
            t1.join(5)
            t2.join(5)

        self.assertEqual(errors, [])
        self.assertEqual(len(calls), 1)
        self.assertEqual(sorted(results), ["<feed>x</feed>", "<feed>x</feed>"])


class PerFeedLockTests(TestCase):
    """update_single_feed takes a per-feed flock; a held lock skips the run."""

    def setUp(self):
        self.feed = Feed.objects.create(
            feed_url="https://example.com/lock.xml",
            name="LockFeed",
            slug="lock-feed",
        )
        self.lock_path = cmd._feed_lock_path(self.feed)

    def tearDown(self):
        try:
            import os

            os.unlink(self.lock_path)
        except OSError:
            pass

    @patch("core.management.commands.feed_updater.close_old_connections")
    @patch("core.management.commands.feed_updater.handle_single_feed_fetch")
    def test_concurrent_run_is_skipped_not_failed(
        self, mock_fetch, mock_close_conn
    ):
        with open(self.lock_path, "a") as holder:
            fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                # Skipping while another process updates the same feed is a
                # success: the holder is doing the same work.
                self.assertTrue(cmd.update_single_feed(self.feed))
            finally:
                fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        mock_fetch.assert_not_called()

    @patch("core.management.commands.feed_updater.close_old_connections")
    @patch("core.management.commands.feed_updater.handle_single_feed_fetch")
    def test_mocked_feed_without_translation_status_defaults_to_success(
        self, mock_fetch, mock_close_conn
    ):
        """A bare mock feed (no DB row, no flags) still counts as updated
        when the pipeline ran without marking anything failed."""
        feed = Mock()
        feed.slug = "mock-feed"
        feed.name = "Mock"
        feed.translate_title = False
        feed.translate_content = False
        feed.summary = False
        feed.translation_status = None
        feed.pk = None  # _feed_lock_path falls back to slug

        self.assertTrue(cmd.update_single_feed(feed))

    @patch("core.management.commands.feed_updater.close_old_connections")
    @patch("core.management.commands.feed_updater.handle_single_feed_fetch")
    def test_released_lock_allows_next_run(self, mock_fetch, mock_close_conn):
        with open(self.lock_path, "a") as holder:
            fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertTrue(cmd.update_single_feed(self.feed))
            fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        mock_fetch.reset_mock()
        # Lock released: the next update proceeds.
        self.assertTrue(cmd.update_single_feed(self.feed))
        mock_fetch.assert_called_once_with(self.feed)


class EntryRefreshOnFetchTests(TestCase):
    """Re-served items update the existing row instead of duplicating."""

    def setUp(self):
        self.feed = Feed.objects.create(
            feed_url="https://example.com/re.xml", name="Re", max_posts=20
        )

    def _entry_data(self, guid, title, published, updated, content="c"):
        import time as time_mod

        def st(dt):
            return dt.timetuple() if dt else None

        return {
            "id": guid,
            "link": f"https://example.com/{guid}",
            "title": title,
            "published_parsed": st(published),
            "updated_parsed": st(updated),
            "summary": content,
            "author": "a",
        }

    def test_existing_guid_refreshes_fields_and_updated(self):
        from core.tasks.fetch_feeds import _process_feed_entries

        old = timezone.now() - timedelta(days=5)
        entry = Entry.objects.create(
            feed=self.feed,
            guid="g1",
            link="https://example.com/g1",
            original_title="Old Title",
            original_content="old",
            translated_title="旧标题",
            translated_content="旧正文",
            ai_summary="旧摘要",
            pubdate=old,
            updated=old,
        )
        new_updated = timezone.now()
        entries = [self._entry_data("g1", "New Title", old, new_updated, "new")]

        _process_feed_entries(self.feed, entries)

        entry.refresh_from_db()
        self.assertEqual(Entry.objects.count(), 1)
        self.assertEqual(entry.original_title, "New Title")
        self.assertEqual(entry.original_content, "new")
        self.assertEqual(entry.updated.date(), new_updated.date())
        # The changed source also wiped the stale derived AI text so it
        # gets regenerated against the new content.
        self.assertIsNone(entry.translated_title)
        self.assertIsNone(entry.translated_content)
        self.assertIsNone(entry.ai_summary)

    def test_unchanged_source_keeps_derived_text(self):
        """An identical re-serve refreshes timestamps but must not wipe a
        paid-for translation/summary."""
        from core.tasks.fetch_feeds import _process_feed_entries

        old = timezone.now() - timedelta(days=5)
        entry = Entry.objects.create(
            feed=self.feed,
            guid="g1",
            link="https://example.com/g1",
            original_title="Same Title",
            original_content="c",
            translated_title="标题",
            ai_summary="摘要",
            pubdate=old,
            updated=old,
        )
        new_updated = timezone.now()
        entries = [self._entry_data("g1", "Same Title", old, new_updated, "c")]

        _process_feed_entries(self.feed, entries)

        entry.refresh_from_db()
        self.assertEqual(entry.translated_title, "标题")
        self.assertEqual(entry.ai_summary, "摘要")

    def test_duplicate_guid_in_one_feed_only_inserts_once(self):
        from core.tasks.fetch_feeds import _process_feed_entries

        now = timezone.now()
        entries = [
            self._entry_data("dup", "A", now, now),
            self._entry_data("dup", "B", now, now),
        ]
        _process_feed_entries(self.feed, entries)
        self.assertEqual(Entry.objects.filter(feed=self.feed, guid="dup").count(), 1)

    def test_missing_updated_parsed_keeps_existing_updated(self):
        """A feed that stops serving <updated> must not erase the stored
        timestamp — Filter.needs_re_evaluation depends on it."""
        from core.tasks.fetch_feeds import _process_feed_entries

        old = timezone.now() - timedelta(days=2)
        entry = Entry.objects.create(
            feed=self.feed,
            guid="g1",
            link="https://example.com/g1",
            original_title="T",
            pubdate=old,
            updated=old,
        )
        # Re-fetch the same item without updated_parsed.
        entries = [self._entry_data("g1", "T2", old, None)]
        _process_feed_entries(self.feed, entries)

        entry.refresh_from_db()
        self.assertEqual(entry.updated, old)


class XmlEmptyFeedTests(TestCase):
    """A missing/empty feed returns 404 for XML too, not a 200 <error>."""

    def test_make_response_xml_empty_returns_404(self):
        from core.views import _make_response

        resp = _make_response(None, "x", "xml")
        self.assertEqual(resp.status_code, 404)

    def test_make_response_json_empty_returns_404(self):
        from core.views import _make_response

        resp = _make_response(None, "x", "json")
        self.assertEqual(resp.status_code, 404)


class CleanupOrderTests(TestCase):
    """Cleanup keeps the latest entries by pubdate, matching display."""

    def test_keeps_latest_by_pubdate_not_insertion_order(self):
        feed = Feed.objects.create(
            feed_url="https://example.com/c.xml", name="C", max_posts=2
        )
        now = timezone.now()
        # Inserted newest-first: lowest id has the newest pubdate.
        e_new = Entry.objects.create(
            feed=feed, link="https://example.com/1", pubdate=now
        )
        e_mid = Entry.objects.create(
            feed=feed, link="https://example.com/2",
            pubdate=now - timedelta(days=1),
        )
        e_old = Entry.objects.create(
            feed=feed, link="https://example.com/3",
            pubdate=now - timedelta(days=2),
        )
        cleanup_feed_entries(feed)
        remaining = set(feed.entries.values_list("id", flat=True))
        # By -id, e_old would have been kept and e_new deleted.
        self.assertEqual(remaining, {e_new.id, e_mid.id})
        self.assertNotIn(e_old.id, remaining)


class EntryIndexTests(TestCase):
    def test_feed_pubdate_and_guid_indexes_exist(self):
        fields = {tuple(idx.fields) for idx in Entry._meta.indexes}
        self.assertIn(("feed", "-pubdate"), fields)
        self.assertIn(("feed", "guid"), fields)


class FetchArticleInvalidatesDerivedTextTests(TestCase):
    """A changed fetched article clears stale translation/summary."""

    def setUp(self):
        from core.models import OpenAIAgent

        self.agent = OpenAIAgent.objects.create(name="A", api_key="k")
        self.feed = Feed.objects.create(
            name="F",
            feed_url="https://example.com/fa.xml",
            translator=self.agent,
            translate_content=True,
            fetch_article=True,
        )
        self.entry = Entry.objects.create(
            feed=self.feed,
            link="https://example.com/e",
            original_title="T",
            original_content="old body",
            translated_title="旧标题",
            ai_summary="旧摘要",
        )

    def test_changed_source_clears_stale_derived_fields(self):
        from core.tasks.translate_feeds import translate_feed

        with (
            patch(
                "core.tasks.translate_feeds._fetch_article_content",
                return_value="new body",
            ),
            patch.object(
                type(self.agent),
                "translate",
                return_value={"text": "新正文", "tokens": 5, "characters": 5},
            ),
        ):
            translate_feed(self.feed, target_field="content")

        self.entry.refresh_from_db()
        self.assertEqual(self.entry.original_content, "new body")
        self.assertEqual(self.entry.translated_content, "新正文")
        # Stale derived text from the previous source must not survive.
        self.assertIsNone(self.entry.translated_title)
        self.assertIsNone(self.entry.ai_summary)


class BatchModifyBumpTests(TestCase):
    """feed_batch_modify must invalidate rendered caches for every output-
    sensitive field, not just tags/filter reassignment."""

    def setUp(self):
        cache.clear()
        self.feed = Feed.objects.create(
            name="F", feed_url="https://example.com/b.xml", slug="bm-feed"
        )

    def tearDown(self):
        cache.clear()

    def test_update_fields_bump_rss_version(self):
        from core.actions import feed_batch_modify
        from django.test import RequestFactory
        from django.contrib.auth.models import User
        from django.contrib.messages.storage.fallback import FallbackStorage

        User.objects.create_superuser("adm", "a@b.c", "pw")
        request = RequestFactory().post(
            "/",
            {
                "apply": "1",
                "max_posts": "Change",
                "max_posts_value": "5",
                "_selected_action": [str(self.feed.pk)],
                "action": "feed_batch_modify",
            },
        )
        request.user = User.objects.get(username="adm")
        request.session = "session"
        request._messages = FallbackStorage(request)

        before = cache_version("rss", self.feed.pk)
        feed_batch_modify(None, request, Feed.objects.filter(pk=self.feed.pk))

        self.assertGreater(cache_version("rss", self.feed.pk), before)
        self.feed.refresh_from_db()
        self.assertEqual(self.feed.max_posts, 5)

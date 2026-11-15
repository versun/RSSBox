"""Regression tests for issue #181.

Two coupled defects made translated titles vanish and translations expire
repeatedly:

1. ``translate_feed`` cleared ``translated_title`` whenever a fetched full
   article differed from the stored content, so the title translation paid
   for earlier in the same run was persisted as NULL.
2. ``_process_feed_entries`` compared the re-served RSS payload against
   ``original_content``, which the translation pipeline had already
   replaced with the fetched full article. Every re-fetch therefore looked
   like a publisher update, wiped the paid translations and re-ran the
   whole pipeline.
"""

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from core.models import Entry, Feed, OpenAIAgent
from core.tasks.fetch_feeds import _process_feed_entries, _source_hash
from core.tasks.translate_feeds import translate_feed


def _rss_item(guid, title, published, content="rss summary"):
    """One feedparser-shaped entry as a publisher would serve it."""

    def struct_time(value):
        return value.timetuple() if value else None

    return {
        "id": guid,
        "link": f"https://example.com/{guid}",
        "title": title,
        "published_parsed": struct_time(published),
        "updated_parsed": None,
        "summary": content,
        "author": "author",
    }


class TranslationFixtureMixin:
    def setUp(self):
        self.agent = OpenAIAgent.objects.create(name="A", api_key="k")
        self.feed = Feed.objects.create(
            name="F",
            feed_url="https://example.com/f.xml",
            translator=self.agent,
            translate_title=True,
            translate_content=True,
            fetch_article=True,
            max_posts=20,
        )
        self.published = timezone.now() - timedelta(hours=1)

    def _entry(self, **overrides):
        values = {
            "feed": self.feed,
            "guid": "g1",
            "link": "https://example.com/g1",
            "original_title": "English Title",
            "original_content": "rss summary",
            "original_summary": "rss summary",
            "pubdate": self.published,
            "updated": self.published,
            "source_hash": _source_hash("rss summary"),
        }
        values.update(overrides)
        return Entry.objects.create(**values)


class FullArticleKeepsTitleTranslationTests(TranslationFixtureMixin, TestCase):
    """Issue #181, path 1: fetching the body must not drop the title."""

    def test_title_translation_survives_content_pass(self):
        entry = self._entry()
        with (
            patch(
                "core.tasks.translate_feeds._fetch_article_content",
                return_value="full article body",
            ),
            patch.object(
                type(self.agent),
                "translate",
                side_effect=[
                    {"text": "中文标题", "tokens": 1, "characters": 1},
                    {"text": "中文正文", "tokens": 2, "characters": 2},
                ],
            ),
        ):
            translate_feed(self.feed, target_field="title")
            translate_feed(self.feed, target_field="content")

        entry.refresh_from_db()
        self.assertEqual(entry.translated_title, "中文标题")
        self.assertEqual(entry.translated_content, "中文正文")
        self.assertEqual(entry.original_content, "full article body")

    def test_failed_body_translation_keeps_title_for_retry(self):
        """A failed body translation leaves the entry pending, but the
        title already saved by this run must not be collateral damage."""
        entry = self._entry()
        with (
            patch(
                "core.tasks.translate_feeds._fetch_article_content",
                return_value="full article body",
            ),
            patch.object(
                type(self.agent),
                "translate",
                side_effect=[
                    {"text": "中文标题", "tokens": 1, "characters": 1},
                    RuntimeError("provider timeout"),
                ],
            ),
        ):
            translate_feed(self.feed, target_field="title")
            translate_feed(self.feed, target_field="content")

        entry.refresh_from_db()
        self.assertEqual(entry.translated_title, "中文标题")
        self.assertIsNone(entry.translated_content)
        self.assertFalse(self.feed.translation_status)


class RefetchAfterFullArticleTests(TranslationFixtureMixin, TestCase):
    """Issue #181, path 2: the RSS payload must be compared with itself."""

    def _store_full_article(self):
        """Run the pipeline once so the row holds a fetched full article."""
        entry = self._entry(
            translated_title="中文标题",
            translated_content="中文正文",
            ai_summary="中文摘要",
        )
        Entry.objects.filter(pk=entry.pk).update(original_content="full article body")
        return Entry.objects.get(pk=entry.pk)

    def test_identical_reserve_keeps_paid_translations(self):
        entry = self._store_full_article()

        _process_feed_entries(
            self.feed, [_rss_item("g1", "English Title", self.published)]
        )

        entry.refresh_from_db()
        self.assertEqual(entry.translated_title, "中文标题")
        self.assertEqual(entry.translated_content, "中文正文")
        self.assertEqual(entry.ai_summary, "中文摘要")
        # The richer fetched body survives an unchanged re-serve.
        self.assertEqual(entry.original_content, "full article body")
        # Nothing changed, so readers must not see a new <updated>.
        self.assertEqual(entry.updated, self.published)

    def test_reserve_does_not_trigger_refetch_or_retranslate(self):
        self._store_full_article()
        _process_feed_entries(
            self.feed, [_rss_item("g1", "English Title", self.published)]
        )

        with (
            patch(
                "core.tasks.translate_feeds._fetch_article_content",
            ) as fetcher,
            patch.object(type(self.agent), "translate") as provider,
        ):
            translate_feed(self.feed, target_field="title")
            translate_feed(self.feed, target_field="content")
            fetcher.assert_not_called()
            provider.assert_not_called()

    def test_title_only_change_clears_title_only(self):
        entry = self._store_full_article()

        _process_feed_entries(
            self.feed, [_rss_item("g1", "Renamed Title", self.published)]
        )

        entry.refresh_from_db()
        self.assertEqual(entry.original_title, "Renamed Title")
        self.assertIsNone(entry.translated_title)
        self.assertEqual(entry.translated_content, "中文正文")
        self.assertEqual(entry.ai_summary, "中文摘要")
        self.assertEqual(entry.original_content, "full article body")

    def test_content_only_change_clears_content_and_summary(self):
        entry = self._store_full_article()

        _process_feed_entries(
            self.feed,
            [
                _rss_item(
                    "g1", "English Title", self.published, content="edited summary"
                )
            ],
        )

        entry.refresh_from_db()
        self.assertEqual(entry.translated_title, "中文标题")
        self.assertIsNone(entry.translated_content)
        self.assertIsNone(entry.ai_summary)
        # The new upstream payload replaces the old article text.
        self.assertEqual(entry.original_content, "edited summary")
        self.assertEqual(entry.source_hash, _source_hash("edited summary"))

    def test_both_changed_clears_both(self):
        entry = self._store_full_article()

        _process_feed_entries(
            self.feed,
            [
                _rss_item(
                    "g1", "Renamed Title", self.published, content="edited summary"
                )
            ],
        )

        entry.refresh_from_db()
        self.assertIsNone(entry.translated_title)
        self.assertIsNone(entry.translated_content)
        self.assertIsNone(entry.ai_summary)


class LegacyRowAdoptionTests(TranslationFixtureMixin, TestCase):
    """Rows written before ``source_hash`` existed must not be mass-wiped
    on upgrade: one adoption pass stores the fingerprint and keeps the
    paid output."""

    def test_null_hash_is_adopted_without_wiping(self):
        entry = self._entry(
            source_hash=None,
            translated_title="中文标题",
            translated_content="中文正文",
            ai_summary="中文摘要",
        )
        Entry.objects.filter(pk=entry.pk).update(original_content="full article body")

        _process_feed_entries(
            self.feed, [_rss_item("g1", "English Title", self.published)]
        )

        entry.refresh_from_db()
        self.assertEqual(entry.source_hash, _source_hash("rss summary"))
        self.assertEqual(entry.translated_title, "中文标题")
        self.assertEqual(entry.translated_content, "中文正文")
        self.assertEqual(entry.original_content, "full article body")

        # From now on the fingerprint is authoritative: a real change is
        # detected on the next re-serve.
        _process_feed_entries(
            self.feed,
            [_rss_item("g1", "English Title", self.published, content="new summary")],
        )
        entry.refresh_from_db()
        self.assertIsNone(entry.translated_content)
        self.assertEqual(entry.translated_title, "中文标题")

    def test_legacy_row_detects_change_from_publisher_summary(self):
        """``original_summary`` is still the publisher's own text, so an
        adopted legacy row can tell a real payload change from a re-serve."""
        entry = self._entry(
            source_hash=None,
            translated_title="中文标题",
            translated_content="中文正文",
            ai_summary="中文摘要",
        )
        Entry.objects.filter(pk=entry.pk).update(original_content="full article body")

        _process_feed_entries(
            self.feed,
            [_rss_item("g1", "English Title", self.published, content="new summary")],
        )

        entry.refresh_from_db()
        self.assertEqual(entry.translated_title, "中文标题")
        self.assertIsNone(entry.translated_content)
        self.assertIsNone(entry.ai_summary)
        self.assertEqual(entry.original_content, "new summary")

    def test_legacy_row_without_article_fetch_compares_text(self):
        """Without full-article fetching ``original_content`` is the RSS
        payload, so a legacy row can still detect a real change."""
        self.feed.fetch_article = False
        self.feed.save(update_fields=["fetch_article"])
        entry = self._entry(
            source_hash=None,
            translated_title="中文标题",
            translated_content="中文正文",
            ai_summary="中文摘要",
        )

        _process_feed_entries(
            self.feed,
            [_rss_item("g1", "English Title", self.published, content="new summary")],
        )

        entry.refresh_from_db()
        self.assertIsNone(entry.translated_content)
        self.assertIsNone(entry.ai_summary)
        self.assertEqual(entry.translated_title, "中文标题")
        self.assertEqual(entry.original_content, "new summary")

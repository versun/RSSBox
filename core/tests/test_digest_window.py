from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase
from django.utils import timezone

from core.admin.admin_site import core_admin_site
from core.admin.digest_admin import DigestAdmin
from core.models import Digest, Entry, Feed, OpenAIAgent, Tag
from core.tasks.generate_digests import DigestGenerator


class DigestWindowTests(TestCase):
    def setUp(self):
        self.agent = OpenAIAgent.objects.create(
            name="Agent", api_key="unused", max_tokens=4096
        )
        self.feed = Feed.objects.create(
            name="News feed",
            slug="news-feed",
            feed_url="https://example.com/news.xml",
            summarizer=self.agent,
        )
        tag = Tag.objects.create(name="News")
        self.feed.tags.add(tag)
        self.digest = Digest.objects.create(
            name="Digest", summarizer=self.agent, target_language="English", days_range=3
        )
        self.digest.tags.add(tag)
        self.now = timezone.now()

    def _entry(self, title, age, *, ai_summary=None):
        return Entry.objects.create(
            feed=self.feed,
            link=f"https://example.com/{title}",
            original_title=title,
            original_content=f"Body for {title}",
            ai_summary=ai_summary,
            pubdate=self.now - age,
        )

    def _titles(self, **kwargs):
        return set(
            self.digest.get_articles_for_digest(**kwargs).values_list(
                "original_title", flat=True
            )
        )

    def test_without_a_previous_digest_days_range_sets_the_window(self):
        self._entry("recent", timedelta(days=1))
        self._entry("too-old", timedelta(days=6))
        self.assertEqual(self._titles(), {"recent"})

    def test_window_starts_at_the_last_digest(self):
        # Entries already covered by the previous digest are not repeated.
        self.digest.last_generated = self.now - timedelta(days=1)
        self._entry("covered-last-time", timedelta(days=2))
        self._entry("new-since", timedelta(hours=12))
        self.assertEqual(self._titles(), {"new-since"})

    def test_entry_at_exact_last_generated_timestamp_is_not_repeated(self):
        boundary = self.now - timedelta(hours=1)
        self.digest.last_generated = boundary
        self._entry("exact-boundary", timedelta(hours=1))
        self._entry("after-boundary", timedelta(minutes=30))
        self.assertEqual(self._titles(end_date=self.now), {"after-boundary"})

    def test_entry_at_exact_start_date_boundary_is_not_repeated(self):
        boundary = self.now - timedelta(days=self.digest.days_range + 1)
        self.digest.last_generated = boundary
        self._entry("at-start-boundary", timedelta(days=self.digest.days_range + 1))
        self._entry("after-start-boundary", timedelta(days=self.digest.days_range))
        self.assertEqual(self._titles(end_date=self.now), {"after-start-boundary"})

    def test_skipped_days_are_not_lost(self):
        # A Friday digest followed by a Monday one: the weekend's entries,
        # older than one day, still make it in.
        self.digest.days_range = 3
        self.digest.last_generated = self.now - timedelta(days=3)
        self._entry("saturday", timedelta(days=2))
        self._entry("sunday", timedelta(days=1))
        self.assertEqual(self._titles(), {"saturday", "sunday"})

    def test_days_range_still_caps_a_long_gap(self):
        self.digest.days_range = 2
        self.digest.last_generated = self.now - timedelta(days=10)
        self._entry("within-range", timedelta(days=1))
        self._entry("beyond-range", timedelta(days=5))
        self.assertEqual(self._titles(), {"within-range"})

    def test_force_ignores_last_generated_for_manual_regeneration(self):
        self.digest.last_generated = self.now - timedelta(minutes=5)
        self.digest.save(update_fields=["last_generated"])
        self._entry("earlier-today", timedelta(hours=6), ai_summary="Summary text")

        self.assertEqual(self._titles(end_date=self.now), set())
        self.assertEqual(
            self._titles(end_date=self.now, force=True), {"earlier-today"}
        )

        with patch.object(
            OpenAIAgent,
            "digester",
            return_value={"text": "Regenerated digest", "tokens": 5},
        ):
            result = DigestGenerator(self.digest).generate(force=True)

        self.assertTrue(result["success"], result)

    def test_generate_uses_single_snapshot_across_summary_and_prepare(self):
        # An article that arrives while summaries are running must neither leak
        # into the current digest with an empty summary nor be lost for the next run.
        first = self._entry("first-article", timedelta(hours=2))
        captured_texts = []

        def summarize_and_insert_midrun(entry, **kwargs):
            # Simulate a concurrent feed fetch inserting a newer entry whose
            # pubdate is after the generation start time, plus a backfilled
            # entry with pubdate before now.
            Entry.objects.create(
                feed=self.feed,
                link="https://example.com/arrived-after-start",
                original_title="arrived-after-start",
                original_content="Mid-run body",
                pubdate=timezone.now() + timedelta(seconds=1),
            )
            Entry.objects.create(
                feed=self.feed,
                link="https://example.com/arrived-backfilled",
                original_title="arrived-backfilled",
                original_content="Backfilled body",
                pubdate=first.pubdate + timedelta(minutes=1),
            )
            return "Summary for first", 4

        def fake_digester(text, **kwargs):
            captured_texts.append(text)
            return {"text": "Final digest", "tokens": 5}

        with (
            patch(
                "core.tasks.summarize_feeds._summarize_entry",
                side_effect=summarize_and_insert_midrun,
            ),
            patch.object(OpenAIAgent, "digester", side_effect=fake_digester),
        ):
            result = DigestGenerator(self.digest).generate()

        self.assertTrue(result["success"], result)
        self.assertEqual(len(captured_texts), 1)
        self.assertIn("first-article", captured_texts[0])
        self.assertIn("Summary for first", captured_texts[0])
        self.assertNotIn("arrived-after-start", captured_texts[0])
        self.assertNotIn("arrived-backfilled", captured_texts[0])

        # The entry published after run start is picked up cleanly by the next run.
        self.digest.refresh_from_db()
        next_titles = set(
            self.digest.get_articles_for_digest(
                end_date=timezone.now() + timedelta(seconds=5)
            ).values_list("original_title", flat=True)
        )
        self.assertEqual(next_titles, {"arrived-after-start"})

    def test_last_generated_not_advanced_when_entry_creation_fails(self):
        previous_last_generated = self.now - timedelta(days=1)
        self.digest.last_generated = previous_last_generated
        self.digest.save(update_fields=["last_generated"])
        self._entry("fresh-article", timedelta(hours=2), ai_summary="Summary")

        with (
            patch.object(
                OpenAIAgent,
                "digester",
                return_value={"text": "Digest body", "tokens": 5},
            ),
            patch(
                "core.tasks.generate_digests.Entry.objects.create",
                side_effect=RuntimeError("db write failed"),
            ),
        ):
            result = DigestGenerator(self.digest).generate()

        self.assertFalse(result["success"])
        self.digest.refresh_from_db()
        self.assertFalse(self.digest.status)
        self.assertEqual(self.digest.last_generated, previous_last_generated)

    def test_admin_generate_digest_action_passes_force_true(self):
        admin_instance = DigestAdmin(Digest, core_admin_site)
        request = RequestFactory().post("/admin/core/digest/")
        request.session = {}
        request._messages = FallbackStorage(request)

        with patch(
            "core.tasks.task_manager.task_manager.submit_task",
            return_value=MagicMock(),
        ) as submit_task:
            admin_instance.generate_digest_action(
                request, Digest.objects.filter(pk=self.digest.pk)
            )

        submit_task.assert_called_once()
        _, kwargs = submit_task.call_args
        self.assertEqual(kwargs, {"force": True})

import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext

from core.models import Entry, Feed
from core.models.agent import OpenAIAgent
from core.tasks.summarize_feeds import _save_progress, handle_feeds_summary
from core.tasks.translate_feeds import handle_feeds_translation, translate_feed
from core.tasks.utils import PersistError


class CheckpointTransactionTests(TestCase):
    def translation_fixture(self, **feed_options):
        agent = OpenAIAgent.objects.create(name="Translator", api_key="unused")
        feed = Feed.objects.create(
            name="Translation",
            feed_url="https://example.com/feed",
            translator=agent,
            translate_title=True,
            translate_content=True,
            **feed_options,
        )
        entry = Entry.objects.create(
            feed=feed,
            original_title="Article",
            original_content="Original content",
            link="https://example.com/article",
        )
        return feed, entry

    def assert_no_writes(self, queries):
        writes = [
            q["sql"]
            for q in queries
            if q["sql"].lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE"))
        ]
        self.assertEqual(writes, [])

    def test_completed_translations_do_not_write_or_call_provider(self):
        feed, entry = self.translation_fixture()
        entry.translated_title = "Saved title"
        entry.translated_content = "Saved content"
        entry.save()
        for field in ("title", "content"):
            with (
                self.subTest(field=field),
                patch.object(OpenAIAgent, "translate") as provider,
            ):
                with CaptureQueriesContext(connection) as queries:
                    translate_feed(feed, target_field=field)
                provider.assert_not_called()
                self.assert_no_writes(queries)

    def test_article_fetch_replaces_stale_translation_for_changed_source(self):
        """A fetched article that differs from the stored source invalidates
        the old translation instead of pairing the new source with stale text."""
        feed, entry = self.translation_fixture(fetch_article=True)
        entry.translated_content = "Saved translation"
        entry.save()
        with (
            patch(
                "core.tasks.translate_feeds._fetch_article_content",
                return_value="Updated source",
            ),
            patch.object(
                OpenAIAgent,
                "translate",
                return_value={"text": "New translation", "tokens": 7, "characters": 11},
            ) as provider,
        ):
            translate_feed(feed, target_field="content")
            entry.refresh_from_db()
            self.assertEqual(entry.original_content, "Updated source")
            self.assertEqual(entry.translated_content, "New translation")
            provider.assert_called_once()

    def test_counter_failure_rolls_back_translation(self):
        feed, entry = self.translation_fixture()
        for field in ("title", "content"):
            with (
                self.subTest(field=field),
                patch.object(
                    OpenAIAgent,
                    "translate",
                    return_value={"text": "Translated", "tokens": 7, "characters": 11},
                ),
                patch.object(Feed.objects, "filter") as query,
            ):
                query.return_value.update.side_effect = RuntimeError(
                    "Database unavailable"
                )
                translate_feed(feed, target_field=field)
                self.assertFalse(feed.translation_status)
                entry.refresh_from_db()
                self.assertIsNone(getattr(entry, f"translated_{field}"))
        feed.refresh_from_db()
        self.assertEqual(feed.total_tokens, 0)
        self.assertEqual(feed.total_characters, 0)

    def test_handlers_do_not_reset_counters_from_a_stale_feed(self):
        agent = OpenAIAgent.objects.create(
            name="Agent", max_tokens=4096, api_key="unused"
        )
        feed = Feed.objects.create(
            name="Stale",
            feed_url="https://example.com/feed",
            summarizer=agent,
            translator=agent,
            translate_title=True,
        )
        Entry.objects.create(
            feed=feed, original_title="Article", original_content="Content"
        )
        Feed.objects.filter(pk=feed.pk).update(total_tokens=100)
        with patch.object(
            OpenAIAgent, "summarize", return_value={"text": "Summary", "tokens": 7}
        ):
            handle_feeds_summary([feed])
        self.assertEqual(Feed.objects.get(pk=feed.pk).total_tokens, 107)
        with patch.object(
            OpenAIAgent, "translate", return_value={"text": "Title", "tokens": 7}
        ):
            handle_feeds_translation([feed])
        self.assertEqual(Feed.objects.get(pk=feed.pk).total_tokens, 114)

    def test_counter_failure_rolls_back_summary(self):
        feed = Feed.objects.create(name="Atomic", feed_url="https://example.com/feed")
        entry = Entry.objects.create(feed=feed, original_title="Article")
        entry.ai_summary = "Completed"
        with patch.object(Feed.objects, "filter") as query:
            query.return_value.update.side_effect = RuntimeError("Database unavailable")
            with self.assertRaisesRegex(PersistError, "Could not persist summaries"):
                _save_progress([entry], feed, 7)
        entry.refresh_from_db()
        feed.refresh_from_db()
        self.assertIsNone(entry.ai_summary)
        self.assertEqual(feed.total_tokens, 0)

    def test_checkpoint_does_not_overwrite_a_newer_counter(self):
        feed = Feed.objects.create(name="Counter", feed_url="https://example.com/feed")
        entry = Entry.objects.create(feed=feed, original_title="Article")
        Feed.objects.filter(pk=feed.pk).update(total_tokens=100)
        entry.ai_summary = "Completed"
        _save_progress([entry], feed, 7)
        feed.refresh_from_db()
        self.assertEqual(feed.total_tokens, 107)


class AICheckpointTests(SimpleTestCase):
    def test_completed_entries_survive_hard_exit_and_are_not_repeated(self):
        for field in ("ai_summary", "translated_title", "translated_content"):
            with self.subTest(field=field), TemporaryDirectory() as directory:
                database = str(Path(directory) / "checkpoint.sqlite3")

                def run(operation, expected=0, database=database, field=field):
                    result = subprocess.run(
                        [
                            sys.executable,
                            "-m",
                            "core.tests.ai_checkpoint_worker",
                            database,
                            operation,
                            field,
                        ],
                        capture_output=True,
                        check=False,
                        text=True,
                        timeout=60,
                    )
                    self.assertEqual(
                        result.returncode, expected, result.stdout + result.stderr
                    )
                    return result.stdout

                run("setup")
                run("crash", expected=41)
                saved = json.loads(run("inspect").splitlines()[-1])
                self.assertTrue(saved["values"][0])
                self.assertIsNone(saved["values"][1])
                self.assertEqual(saved["tokens"], 7)
                run("resume")
                resumed = json.loads(run("inspect").splitlines()[-1])
                self.assertTrue(all(resumed["values"]))
                self.assertEqual(resumed["tokens"], 14)
                if field != "ai_summary":
                    self.assertEqual(resumed["characters"], 22)
                calls = Path(database + ".calls").read_text().splitlines()
                self.assertEqual(
                    len(calls), 3
                )  # Completed first, interrupted second, resumed second.
                self.assertNotEqual(calls[0], calls[1])
                self.assertEqual(calls[1], calls[2])

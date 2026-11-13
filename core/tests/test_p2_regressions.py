"""Regression tests for the P2 review fixes.

Covers: digest merge accounting/empty-digest guard (P2-1), persistence-error
short-circuiting (P2-2), chunk-level retry accounting (P2-3), summary_detail
tolerance and chunk-size clamping (P2-4), stale-translation invalidation on
fetch_article (P2-5), filter prompt fallback and counter save (P2-7), digest
summary token attribution and per-entry checkpointing (P2-8), and the CLI
propagating per-feed failures (P2-9).
"""

import io
from concurrent.futures import Future
from datetime import timedelta
from unittest.mock import MagicMock, Mock, patch

from django.test import TestCase
from django.utils import timezone

from core.management.commands import feed_updater as cmd
from core.models import Digest, Entry, Feed, Filter, FilterResult, OpenAIAgent, Tag
from core.tasks.generate_digests import DigestGenerator, _ensure_entries_have_summaries
from core.tasks.summarize_feeds import _summarize_entry, summarize_feed
from core.tasks.translate_feeds import translate_feed
from core.tasks.utils import PersistError, auto_retry


# ---------------------------------------------------------------------------
# P2-1: digest merge accounting + empty/all-failed guards
# ---------------------------------------------------------------------------


class DigestMergeAccountingTests(TestCase):
    def setUp(self):
        self.agent = OpenAIAgent.objects.create(
            name="Digest agent", api_key="key"
        )
        self.feed = Feed.objects.create(
            name="News", feed_url="https://example.com/n.xml", summarizer=self.agent
        )
        self.tag = Tag.objects.create(name="Daily")
        self.feed.tags.add(self.tag)
        self.entry = Entry.objects.create(
            feed=self.feed,
            link="https://example.com/a",
            original_title="Article",
            ai_summary="Existing summary",
            pubdate=timezone.now(),
        )
        self.digest = Digest.objects.create(
            name="Daily digest",
            summarizer=self.agent,
            target_language="English",
            days_range=1,
        )
        self.digest.tags.add(self.tag)

    def _generator(self, chunks, responses):
        """Build a DigestGenerator with a mocked summarizer returning responses."""
        gen = DigestGenerator(self.digest)
        gen.prepare_articles()
        return gen

    def test_merge_tokens_are_counted(self):
        """The merge call's tokens must be added to digest.total_tokens."""
        responses = [
            {"text": "chunk digest 1", "tokens": 10},
            {"text": "chunk digest 2", "tokens": 10},
            {"text": "merged digest", "tokens": 7},
        ]
        with (
            patch.object(
                DigestGenerator,
                "build_prompt",
                return_value=(["articles-1", "articles-2"], "prompt", {}),
            ),
            patch.object(
                OpenAIAgent, "digester", side_effect=responses
            ) as digester,
        ):
            result = DigestGenerator(self.digest).generate()

        self.assertTrue(result["success"])
        self.digest.refresh_from_db()
        # 10 + 10 (chunks) + 7 (merge) = 27
        self.assertEqual(self.digest.total_tokens, 27)
        self.assertEqual(digester.call_count, 3)

    def test_all_chunks_failed_does_not_index_error(self):
        """When every chunk digester call returns empty text, the digest must
        fail cleanly instead of IndexError on digests_list[0]."""
        with (
            patch.object(
                DigestGenerator,
                "build_prompt",
                return_value=(["articles"], "prompt", {}),
            ),
            patch.object(
                OpenAIAgent,
                "digester",
                return_value={"text": "", "tokens": 5},
            ),
        ):
            result = DigestGenerator(self.digest).generate()

        self.assertFalse(result["success"])
        self.assertIn("All digest chunks failed", result["error"])
        self.digest.refresh_from_db()
        self.assertFalse(self.digest.status)
        # Failed digest must not create an entry.
        self.assertEqual(self.digest.get_digest_feed().entries.count(), 0)

    def test_empty_merge_text_is_a_failure(self):
        """A merge that returns empty text must not be published as ''."""
        responses = [
            {"text": "chunk digest 1", "tokens": 10},
            {"text": "chunk digest 2", "tokens": 10},
            {"text": "   ", "tokens": 7},  # merge returned whitespace
        ]
        with (
            patch.object(
                DigestGenerator,
                "build_prompt",
                return_value=(["a1", "a2"], "prompt", {}),
            ),
            patch.object(OpenAIAgent, "digester", side_effect=responses),
        ):
            result = DigestGenerator(self.digest).generate()

        self.assertFalse(result["success"])
        self.assertIn("empty", result["error"].lower())
        self.assertEqual(self.digest.get_digest_feed().entries.count(), 0)


# ---------------------------------------------------------------------------
# P2-2: persistence errors abort the pipeline
# ---------------------------------------------------------------------------


class PersistenceErrorTests(TestCase):
    def setUp(self):
        self.agent = OpenAIAgent.objects.create(
            name="Agent", api_key="key"
        )
        self.feed = Feed.objects.create(
            name="F",
            feed_url="https://example.com/f.xml",
            translator=self.agent,
            summarizer=self.agent,
            translate_title=True,
        )
        self.e1 = Entry.objects.create(
            feed=self.feed,
            link="https://example.com/1",
            original_title="A1",
            original_content="c1",
        )
        self.e2 = Entry.objects.create(
            feed=self.feed,
            link="https://example.com/2",
            original_title="A2",
            original_content="c2",
        )

    def test_database_error_stops_translation_before_next_ai_call(self):
        """If the per-entry save hits a DatabaseError, the feed aborts instead
        of paying for the next entry's translation."""
        from django.db import DatabaseError

        calls = []

        def fake_translate(**kwargs):
            calls.append(1)
            return {"text": "translated", "tokens": 5, "characters": 5}

        with (
            patch.object(OpenAIAgent, "translate", side_effect=fake_translate),
            patch.object(Entry, "save", side_effect=DatabaseError("locked")),
        ):
            translate_feed(self.feed, target_field="title")

        self.assertEqual(len(calls), 1)
        self.assertFalse(self.feed.translation_status)

    def test_persist_error_stops_summary_before_next_ai_call(self):
        """Same abort semantics for the summary pipeline."""
        self.feed.summary = True
        self.feed.save()

        calls = []

        def fake_summarize(**kwargs):
            calls.append(1)
            return {"text": "summary", "tokens": 5}

        with (
            patch.object(OpenAIAgent, "summarize", side_effect=fake_summarize),
            patch(
                "core.tasks.summarize_feeds._save_progress",
                side_effect=PersistError("db gone"),
            ),
        ):
            completed = summarize_feed(self.feed)

        self.assertFalse(completed)
        self.assertEqual(len(calls), 1)

    def test_auto_retry_does_not_retry_persistence_errors(self):
        """auto_retry must not retry PersistError: the DB is down, retrying
        just burns quota."""
        calls = []

        def always_fail(**kwargs):
            calls.append(1)
            raise PersistError("db gone")

        with self.assertRaises(PersistError):
            auto_retry(always_fail, max_retries=3, text="x")

        self.assertEqual(len(calls), 1)


# ---------------------------------------------------------------------------
# P2-3: chunk-level retry does not replay completed chunks
# ---------------------------------------------------------------------------


class ChunkRetryTests(TestCase):
    def setUp(self):
        self.agent = OpenAIAgent.objects.create(
            name="Chunk agent", api_key="key", model="m", max_tokens=600
        )
        self.client = MagicMock()
        self.client.with_options.return_value = self.client
        self.create = self.client.chat.completions.create
        init = patch.object(OpenAIAgent, "_init", return_value=self.client)
        init.start()
        self.addCleanup(init.stop)

    @staticmethod
    def _resp(text="ok", tokens=5):
        return Mock(
            choices=[
                Mock(finish_reason="stop", message=Mock(content=text))
            ],
            usage=Mock(total_tokens=tokens),
        )

    def test_one_flaky_chunk_retries_once_not_whole_text(self):
        """A single failing chunk must retry that chunk once, not replay the
        entire multi-chunk completion."""
        token_calls = []

        def token_count(text):
            token_calls.append(text)
            if text == "long text":
                return 2000
            if text == "system":
                return 50
            return 10

        with (
            patch("core.models.agent.get_token_count", side_effect=token_count),
            patch(
                "core.models.agent.adaptive_chunking",
                return_value=["chunk one", "chunk two"],
            ),
        ):
            # First chunk succeeds, second chunk's first attempt fails, its
            # retry succeeds.
            self.create.side_effect = [
                self._resp("one"),
                RuntimeError("flaky"),
                self._resp("two"),
            ]
            result = self.agent.completions("long text", system_prompt="system")

        self.assertEqual(result["text"], "one two")
        # 3 provider calls total (chunk1 ok, chunk2 fail, chunk2 retry)
        self.assertEqual(self.create.call_count, 3)

    def test_completed_chunks_are_not_replayed_on_later_chunk_failure(self):
        """If chunk 2 keeps failing, chunk 1's provider call is not replayed."""
        call_texts = []

        def token_count(text):
            if text == "long text":
                return 2000
            if text == "system":
                return 50
            return 10

        def capture(messages, **kwargs):
            call_texts.append(messages[-1]["content"])
            if "chunk two" in messages[-1]["content"]:
                raise RuntimeError("chunk two down")
            return self._resp("one")

        with (
            patch("core.models.agent.get_token_count", side_effect=token_count),
            patch(
                "core.models.agent.adaptive_chunking",
                return_value=["chunk one", "chunk two"],
            ),
        ):
            self.create.side_effect = capture
            with self.assertRaises(RuntimeError):
                self.agent.completions("long text", system_prompt="system")

        # chunk one called once; chunk two tried twice then gave up.
        self.assertEqual(call_texts.count("chunk one"), 1)
        self.assertEqual(call_texts.count("chunk two"), 2)

    def test_chunked_failure_is_not_retried_by_auto_retry(self):
        """auto_retry must not replay a chunked completion: that would pay
        for the already-succeeded chunks again."""
        calls = []

        def chunked_fail(**kwargs):
            calls.append(1)
            err = RuntimeError("chunk failed twice")
            err.is_chunked_failure = True
            raise err

        with self.assertRaises(RuntimeError):
            auto_retry(chunked_fail, max_retries=3, text="x")

        self.assertEqual(len(calls), 1)


# ---------------------------------------------------------------------------
# P2-4: summary_detail tolerance + chunk-size clamping
# ---------------------------------------------------------------------------


class SummaryConfigToleranceTests(TestCase):
    def setUp(self):
        self.agent = OpenAIAgent.objects.create(
            name="S agent", api_key="key"
        )
        self.feed = Feed.objects.create(
            name="F", feed_url="https://example.com/s.xml", summarizer=self.agent
        )
        self.entry = Entry.objects.create(
            feed=self.feed,
            link="https://example.com/e",
            original_title="T",
            original_content="content",
        )

    def test_summary_detail_none_does_not_crash(self):
        self.feed.summary_detail = None
        self.feed.save()
        with patch("core.tasks.summarize_feeds.auto_retry") as r:
            r.return_value = {"text": "s", "tokens": 3}
            self.assertTrue(summarize_feed(self.feed))
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.ai_summary, "s")

    def test_zero_chunk_limits_do_not_divide_by_zero(self):
        """A summarizer with max_tokens=0 must not produce ZeroDivisionError
        inside _summarize_entry."""
        agent0 = OpenAIAgent.objects.create(
            name="Zero agent", api_key="key", max_tokens=0
        )
        with patch("core.tasks.summarize_feeds.auto_retry") as r:
            r.return_value = {"text": "s", "tokens": 3}
            summary, tokens = _summarize_entry(
                entry=self.entry,
                summarizer=agent0,
                target_language="English",
                min_chunk_size=agent0.min_size(),
                max_chunk_size=agent0.max_size(),
                summarize_recursively=True,
                max_context_chunks=4,
                max_context_tokens=agent0.max_tokens,
                chunk_delimiter=".",
                max_chunks_per_entry=20,
                summary_detail=0.0,
            )
        self.assertEqual(summary, "s")


# ---------------------------------------------------------------------------
# P2-5: fetch_article invalidates stale translation
# ---------------------------------------------------------------------------


class StaleTranslationTests(TestCase):
    def setUp(self):
        self.agent = OpenAIAgent.objects.create(
            name="T agent", api_key="key"
        )
        self.feed = Feed.objects.create(
            name="F",
            feed_url="https://example.com/t.xml",
            translator=self.agent,
            translate_content=True,
            fetch_article=True,
        )
        self.entry = Entry.objects.create(
            feed=self.feed,
            link="https://example.com/e",
            original_title="T",
            original_content="old source",
            translated_content="old translation",
        )

    def test_changed_source_clears_translation_and_retranslates(self):
        old_updated = self.entry.updated
        with (
            patch(
                "core.tasks.translate_feeds._fetch_article_content",
                return_value="new source",
            ),
            patch.object(
                OpenAIAgent,
                "translate",
                return_value={"text": "new translation", "tokens": 7, "characters": 9},
            ) as provider,
        ):
            translate_feed(self.feed, target_field="content")

        self.entry.refresh_from_db()
        self.assertEqual(self.entry.original_content, "new source")
        self.assertEqual(self.entry.translated_content, "new translation")
        provider.assert_called_once()
        self.assertNotEqual(self.entry.updated, old_updated)

    def test_unchanged_source_keeps_translation_and_skips_ai(self):
        """If fetch_article returns the same source, the checkpoint still
        holds: no provider call, no save."""
        with (
            patch(
                "core.tasks.translate_feeds._fetch_article_content",
                return_value="old source",
            ),
            patch.object(OpenAIAgent, "translate") as provider,
        ):
            translate_feed(self.feed, target_field="content")

        self.entry.refresh_from_db()
        self.assertEqual(self.entry.translated_content, "old translation")
        provider.assert_not_called()


# ---------------------------------------------------------------------------
# P2-7: filter prompt fallback + token counter save
# ---------------------------------------------------------------------------


class FilterFallbackTests(TestCase):
    def setUp(self):
        self.agent = OpenAIAgent.objects.create(
            name="F agent", api_key="key"
        )
        self.feed = Feed.objects.create(
            name="F", feed_url="https://example.com/f.xml"
        )
        self.entry = Entry.objects.create(
            feed=self.feed,
            link="https://example.com/e",
            original_title="T",
            pubdate=timezone.now(),
        )

    def test_none_filter_prompt_uses_default(self):
        rule = Filter.objects.create(
            name="r",
            agent=self.agent,
            filter_method=Filter.AI_ONLY,
            filter_prompt=None,
        )
        with patch.object(
            OpenAIAgent, "filter", return_value={"passed": True, "tokens": 3}
        ) as ai:
            rule.apply_filter(Entry.objects.all())

        self.assertEqual(
            ai.call_args.kwargs["system_prompt"], __import__("config.settings", fromlist=["default_filter_prompt"]).default_filter_prompt
        )

    def test_blocked_result_does_not_count_tokens(self):
        rule = Filter.objects.create(
            name="r2",
            agent=self.agent,
            filter_method=Filter.AI_ONLY,
            filter_prompt="p",
        )
        with patch.object(
            OpenAIAgent, "filter", return_value={"passed": False, "tokens": 9}
        ):
            rule.apply_filter(Entry.objects.all())

        rule.refresh_from_db()
        self.assertEqual(rule.total_tokens, 9)
        # And the FilterResult actually persisted the blocked decision.
        self.assertFalse(rule.results.get(entry=self.entry).passed)


# ---------------------------------------------------------------------------
# P2-8: digest summary token attribution + per-entry checkpoint
# ---------------------------------------------------------------------------


class DigestSummaryAttributionTests(TestCase):
    def setUp(self):
        self.feed_agent = OpenAIAgent.objects.create(
            name="Feed agent", api_key="key"
        )
        self.digest_agent = OpenAIAgent.objects.create(
            name="Digest agent", api_key="key"
        )
        self.feed = Feed.objects.create(
            name="F",
            feed_url="https://example.com/f.xml",
            summarizer=self.feed_agent,
        )
        self.tag = Tag.objects.create(name="T")
        self.feed.tags.add(self.tag)
        self.digest = Digest.objects.create(
            name="D",
            summarizer=self.digest_agent,
            target_language="English",
            days_range=1,
        )
        self.digest.tags.add(self.tag)

    def test_tokens_go_to_the_feed_that_paid_them(self):
        """Summaries made with the feed's own summarizer bill the feed, not
        the digest."""
        for i in range(6):  # > old BATCH_SIZE of 5
            Entry.objects.create(
                feed=self.feed,
                link=f"https://example.com/{i}",
                original_title=f"T{i}",
                pubdate=timezone.now() - timedelta(minutes=5),
            )

        with patch(
            "core.tasks.summarize_feeds._summarize_entry",
            return_value=("summary", 10),
        ):
            _ensure_entries_have_summaries(self.digest)

        self.feed.refresh_from_db()
        self.digest.refresh_from_db()
        # All 6 entries used the feed's summarizer -> billed to the feed.
        self.assertEqual(self.feed.total_tokens, 60)
        self.assertEqual(self.digest.total_tokens, 0)

    def test_fallback_summarizer_bills_the_digest(self):
        """Entries whose feed has no summarizer bill the digest's."""
        self.feed.summarizer = None
        self.feed.save()
        Entry.objects.create(
            feed=self.feed,
            link="https://example.com/0",
            original_title="T0",
            pubdate=timezone.now() - timedelta(minutes=5),
        )

        with patch(
            "core.tasks.summarize_feeds._summarize_entry",
            return_value=("summary", 7),
        ):
            _ensure_entries_have_summaries(self.digest)

        self.feed.refresh_from_db()
        self.digest.refresh_from_db()
        self.assertEqual(self.digest.total_tokens, 7)
        self.assertEqual(self.feed.total_tokens, 0)

    def test_late_failure_keeps_earlier_saved_summaries(self):
        """The per-entry checkpoint means a failure on entry N cannot lose
        the summaries already saved for entries 1..N-1."""
        entries = [
            Entry.objects.create(
                feed=self.feed,
                link=f"https://example.com/{i}",
                original_title=f"T{i}",
                pubdate=timezone.now() - timedelta(minutes=5),
            )
            for i in range(4)
        ]

        def summarize(entry, **kwargs):
            if entry.pk == entries[3].pk:
                raise RuntimeError("provider down")
            return "summary", 5

        with patch(
            "core.tasks.summarize_feeds._summarize_entry", side_effect=summarize
        ):
            failed = _ensure_entries_have_summaries(self.digest)

        self.assertEqual(failed, [entries[3].pk])
        for entry in entries[:3]:
            entry.refresh_from_db()
            self.assertEqual(entry.ai_summary, "summary")
        entries[3].refresh_from_db()
        self.assertIsNone(entries[3].ai_summary)

    def test_persistence_failure_aborts_digest_summaries(self):
        """A DatabaseError on the per-entry save must stop the loop instead
        of paying for the next entry's summary."""
        from django.db import DatabaseError

        entries = [
            Entry.objects.create(
                feed=self.feed,
                link=f"https://example.com/{i}",
                original_title=f"T{i}",
                pubdate=timezone.now() - timedelta(minutes=5),
            )
            for i in range(3)
        ]
        calls = []

        def summarize(entry, **kwargs):
            calls.append(entry.pk)
            return "summary", 5

        with (
            patch(
                "core.tasks.summarize_feeds._summarize_entry",
                side_effect=summarize,
            ),
            patch.object(
                Entry, "save", side_effect=DatabaseError("locked")
            ),
        ):
            failed = _ensure_entries_have_summaries(self.digest)

        # The first entry whose save failed aborts the loop; entries 2/3
        # were never paid for.
        self.assertEqual(len(calls), 1)
        self.assertEqual(failed, calls)


# ---------------------------------------------------------------------------
# P2-9: per-feed failures surface to the CLI
# ---------------------------------------------------------------------------


class CLIFailurePropagationTests(TestCase):
    def test_failed_feed_returns_false_and_exits_2(self):
        """update_multiple_feeds must return False and the command must exit
        non-zero when a feed's AI pipeline reports failure."""
        feed = Mock()
        feed.name = "Feed 1"
        feed.slug = "feed-1"
        feed.tags.values_list.return_value = []

        future = Future()
        future.set_running_or_notify_cancel()
        future.set_result(False)

        with (
            patch.object(cmd, "task_manager") as tm,
            patch.object(cmd, "wait", return_value=([future], [])),
            patch.object(cmd, "cache_rss"),
            patch.object(cmd, "cache_tag"),
            patch.object(cmd, "Tag") as tag_model,
        ):
            tm.submit_task.return_value = future
            tag_model.objects.filter.return_value = []
            ok = cmd.update_multiple_feeds([feed])

        self.assertFalse(ok)

    def test_command_exits_2_on_feed_failure(self):
        """Command.handle exits with code 2 when update_feeds_for_frequency
        reports failed feeds."""
        command = cmd.Command(stdout=io.StringIO(), stderr=io.StringIO())
        with (
            patch("core.management.commands.feed_updater.fcntl"),
            patch("core.management.commands.feed_updater.open"),
            patch.object(
                cmd, "update_feeds_for_frequency", return_value=False
            ),
            self.assertRaises(SystemExit) as ctx,
        ):
            command.handle(frequency="hourly")

        self.assertEqual(ctx.exception.code, 2)

    def test_command_exits_0_on_clean_success(self):
        command = cmd.Command(stdout=io.StringIO(), stderr=io.StringIO())
        with (
            patch("core.management.commands.feed_updater.fcntl"),
            patch("core.management.commands.feed_updater.open"),
            patch.object(
                cmd, "update_feeds_for_frequency", return_value=True
            ),
        ):
            command.handle(frequency="hourly")
        # No SystemExit -> returned normally.

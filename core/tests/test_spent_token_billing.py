"""Regression tests: usage paid by failed AI attempts is billed, not lost."""

from unittest.mock import MagicMock, Mock, patch

from django.test import TestCase

from core.models import Entry, Feed, OpenAIAgent
from core.tasks.summarize_feeds import _summarize_entry
from core.tasks.translate_feeds import translate_feed
from core.tasks.utils import auto_retry


def _paid_error(tokens):
    err = RuntimeError("failed")
    err.tokens_used = tokens
    return err


@patch("core.tasks.utils.time.sleep")
class AutoRetrySpentTokensTests(TestCase):
    def test_failed_attempts_are_added_to_success(self, _sleep):
        func = Mock(side_effect=[_paid_error(7), {"text": "ok", "tokens": 5}])
        self.assertEqual(auto_retry(func, max_retries=3)["tokens"], 12)

    def test_empty_text_attempt_is_billed(self, _sleep):
        func = Mock(side_effect=[{"text": " ", "tokens": 4}, {"text": "ok", "tokens": 5}])
        result = auto_retry(func, max_retries=3, require_text=True)
        self.assertEqual(result["tokens"], 9)

    def test_final_error_carries_cumulative_spend(self, _sleep):
        func = Mock(side_effect=[_paid_error(3), _paid_error(4)])
        with self.assertRaises(RuntimeError) as ctx:
            auto_retry(func, max_retries=2)
        self.assertEqual(ctx.exception.tokens_used, 7)


class CompletionsSpentTokensTests(TestCase):
    def setUp(self):
        self.agent = OpenAIAgent.objects.create(
            name="Agent", api_key="key", model="m", max_tokens=5000
        )
        client = MagicMock()
        client.with_options.return_value = client
        self.create = client.chat.completions.create
        init = patch.object(OpenAIAgent, "_init", return_value=client)
        init.start()
        self.addCleanup(init.stop)

    def test_truncated_response_reports_spent_tokens(self):
        self.create.return_value = Mock(
            choices=[Mock(finish_reason="length", message=Mock(content="cut"))],
            usage=Mock(total_tokens=42),
        )
        with self.assertRaises(RuntimeError) as ctx:
            self.agent.completions("text", system_prompt="system")
        self.assertEqual(ctx.exception.tokens_used, 42)


class SummaryChunkSpentTokensTests(TestCase):
    def test_later_chunk_failure_carries_earlier_chunk_tokens(self):
        summarizer = Mock()
        # Chunk 1 succeeds; every attempt (3) for chunk 2 fails after paying 3.
        summarizer.summarize.side_effect = [
            {"text": "first", "tokens": 10},
            _paid_error(3),
            _paid_error(3),
            _paid_error(3),
        ]
        entry = Mock(original_content="x " * 2000)
        with (
            patch(
                "core.tasks.summarize_feeds.text_handler.adaptive_chunking",
                return_value=["a", "b"],
            ),
            patch("core.tasks.utils.time.sleep"),
            self.assertRaises(RuntimeError) as ctx,
        ):
            _summarize_entry(
                entry=entry,
                summarizer=summarizer,
                target_language="English",
                min_chunk_size=10,
                max_chunk_size=100,
                summarize_recursively=False,
                max_context_chunks=4,
                max_context_tokens=1000,
                chunk_delimiter=".",
                max_chunks_per_entry=20,
                summary_detail=1.0,
            )
        self.assertEqual(ctx.exception.tokens_used, 10 + 3 * 3)


class MissingTranslatorTests(TestCase):
    def test_no_translator_stops_before_touching_entries(self):
        feed = Feed.objects.create(
            name="F", feed_url="https://example.com/f.xml", translate_title=True
        )
        Entry.objects.create(feed=feed, link="https://example.com/1", original_title="t")
        with patch("core.tasks.translate_feeds._translate_entry_title") as translate:
            translate_feed(feed, target_field="title")
        translate.assert_not_called()
        self.assertFalse(feed.translation_status)
        self.assertIn("Translate Engine Not Set", feed.log)

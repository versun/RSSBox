from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from django.test import TestCase
from django.utils import timezone
from openai import APITimeoutError, BadRequestError

from core.models import Entry, Feed
from core.models.agent import OpenAIAgent
from core.tasks.summarize_feeds import handle_feeds_summary
from core.tasks.translate_feeds import handle_feeds_translation
from core.tasks.utils import auto_retry


class AIFailureRecoveryTests(TestCase):
    def setUp(self):
        self.agent = OpenAIAgent.objects.create(
            name="Recovery test",
            api_key="unused",
            max_tokens=4096,
            rate_limit_rpm=0,
        )
        self.feed = Feed.objects.create(
            name="Recovery feed",
            feed_url="https://example.com/feed",
            summarizer=self.agent,
            translator=self.agent,
            translate_title=True,
            translate_content=True,
            max_posts=10,
        )
        self.client = Mock()
        self.client.with_options.return_value = self.client
        self.request = self.client.chat.completions.create
        self.init = patch.object(OpenAIAgent, "_init", return_value=self.client)
        self.init.start()
        self.addCleanup(self.init.stop)
        sleep = patch("core.tasks.utils.time.sleep")
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)

    def response(self, text="A useful result", reason="stop"):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason=reason, message=SimpleNamespace(content=text)
                )
            ],
            usage=SimpleNamespace(total_tokens=12),
        )

    def entry(self, number=0):
        return Entry.objects.create(
            feed=self.feed,
            original_title=f"Title {number}",
            original_content=f"Article {number}.",
            link=f"https://example.com/{number}",
            pubdate=timezone.now() - timedelta(minutes=number),
        )

    def timeout(self):
        return APITimeoutError(request=httpx.Request("POST", "https://example.com"))

    def test_timeout_is_retried_and_success_is_saved(self):
        entry = self.entry()
        self.request.side_effect = [self.timeout(), self.response()]
        handle_feeds_summary([self.feed])
        entry.refresh_from_db()
        self.feed.refresh_from_db()
        self.assertEqual(self.request.call_count, 2)
        self.assertEqual(entry.ai_summary, "A useful result")
        self.assertTrue(self.feed.translation_status)

    def test_exhausted_summary_remains_pending_and_succeeds_next_run(self):
        entry = self.entry()
        self.request.side_effect = self.timeout()
        handle_feeds_summary([self.feed])
        entry.refresh_from_db()
        self.feed.refresh_from_db()
        self.assertIsNone(entry.ai_summary)
        self.assertFalse(self.feed.translation_status)
        self.assertNotIn("Summary Completed", self.feed.log)
        self.assertEqual(self.request.call_count, 3)
        self.assertEqual(self.sleep.call_count, 2)

        self.request.side_effect = None
        self.request.return_value = self.response()
        handle_feeds_summary([self.feed])
        entry.refresh_from_db()
        self.assertEqual(entry.ai_summary, "A useful result")

    def test_empty_and_incomplete_responses_are_not_saved_as_summaries(self):
        entry = self.entry()
        for text, reason in [("", "stop"), ("  ", "stop"), ("partial", "length")]:
            with self.subTest(text=text, reason=reason):
                self.request.return_value = self.response(text, reason)
                handle_feeds_summary([self.feed])
                entry.refresh_from_db()
                self.feed.refresh_from_db()
                self.assertIsNone(entry.ai_summary)
                self.assertFalse(self.feed.translation_status)

    def test_summary_failure_is_not_hidden_by_later_success(self):
        failed, good = self.entry(0), self.entry(1)
        self.request.side_effect = [self.timeout()] * 3 + [self.response()]
        handle_feeds_summary([self.feed])
        failed.refresh_from_db()
        good.refresh_from_db()
        self.feed.refresh_from_db()
        self.assertIsNone(failed.ai_summary)
        self.assertEqual(good.ai_summary, "A useful result")
        self.assertFalse(self.feed.translation_status)

    def test_translation_failure_is_not_hidden_by_later_success(self):
        failed, good = self.entry(0), self.entry(1)
        self.request.side_effect = [self.timeout()] * 3 + [self.response()]
        handle_feeds_translation([self.feed], target_field="title")
        failed.refresh_from_db()
        good.refresh_from_db()
        self.feed.refresh_from_db()
        self.assertIsNone(failed.translated_title)
        self.assertEqual(good.translated_title, "A useful result")
        self.assertFalse(self.feed.translation_status)
        self.assertNotIn("Translate Completed", self.feed.log)
        self.assertIsNone(self.feed.last_translate)

    def test_empty_translation_is_a_failure(self):
        entry = self.entry()
        for field in ("title", "content"):
            with self.subTest(field=field):
                self.request.return_value = self.response("")
                handle_feeds_translation([self.feed], target_field=field)
                entry.refresh_from_db()
                self.feed.refresh_from_db()
                self.assertIsNone(getattr(entry, f"translated_{field}"))
                self.assertFalse(self.feed.translation_status)

    def test_bad_request_is_not_retried(self):
        response = httpx.Response(
            400, request=httpx.Request("POST", "https://example.com")
        )
        self.request.side_effect = BadRequestError(
            "invalid request", response=response, body=None
        )
        with self.assertRaises(BadRequestError):
            auto_retry(self.agent.summarize, text="Article", target_language="English")
        self.assertEqual(self.request.call_count, 1)
        self.sleep.assert_not_called()

    def test_retry_exhaustion_raises_last_error(self):
        func = Mock(side_effect=self.timeout())
        with self.assertRaises(APITimeoutError):
            auto_retry(func)
        self.assertEqual(func.call_count, 3)
        self.assertEqual(self.sleep.call_count, 2)

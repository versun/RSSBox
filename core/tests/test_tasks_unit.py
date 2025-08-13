from django.test import TestCase
from unittest import mock

from core.tasks import _auto_retry, _translate_title
from core.models import Feed, Entry


class TasksUnitTests(TestCase):
    """Isolated unit tests for helpers inside core.tasks."""

    def test_auto_retry_retries_then_success(self):
        calls = {"count": 0}

        def flaky(**kwargs):
            calls["count"] += 1
            if calls["count"] < 3:
                raise ValueError("fail")
            return {"success": True}

        with mock.patch("time.sleep") as mock_sleep:
            result = _auto_retry(flaky, max_retries=5)

        # should have tried 3 times (2 failures + 1 success)
        self.assertEqual(calls["count"], 3)
        self.assertTrue(result["success"])  # final result returned
        # time.sleep called twice for backoff
        self.assertEqual(mock_sleep.call_count, 2)

    def test_translate_title_sets_fields_and_metrics(self):
        feed = Feed.objects.create(feed_url="https://example.com/rss.xml")
        entry = Entry.objects.create(
            feed=feed,
            link="https://example.com/post",
            original_title="Hello",
        )

        class DummyAgent:
            def translate(self, **kwargs):
                return {"text": "你好", "tokens": 10, "characters": 5}

        agent = DummyAgent()
        metrics = _translate_title(entry, target_language="Chinese", engine=agent)

        self.assertEqual(entry.translated_title, "你好")
        self.assertEqual(metrics["tokens"], 10)
        self.assertEqual(metrics["characters"], 5)

        # calling again should early-return (already translated)
        metrics2 = _translate_title(entry, target_language="Chinese", engine=agent)
        self.assertEqual(metrics2, {"tokens": 0, "characters": 0})

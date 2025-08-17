from django.test import TestCase
from unittest import mock

from core.tasks import _auto_retry, _translate_title
from core.models import Feed, Entry


class TasksUnitTests(TestCase):
    """Isolated unit tests for helper functions inside core.tasks."""

    def setUp(self):
        """Set up test data."""
        self.feed = Feed.objects.create(feed_url="https://example.com/rss.xml")
        self.entry = Entry.objects.create(
            feed=self.feed,
            link="https://example.com/post",
            original_title="Hello World",
        )

    def test_auto_retry_succeeds_after_failures(self):
        """Test _auto_retry succeeds after some failures."""
        calls = {"count": 0}

        def flaky_function(**kwargs):
            calls["count"] += 1
            if calls["count"] < 3:
                raise ValueError("fail")
            return {"success": True, "data": "result"}

        with mock.patch("time.sleep") as mock_sleep:
            result = _auto_retry(flaky_function, max_retries=5)

        self.assertEqual(calls["count"], 3)
        self.assertTrue(result["success"])
        self.assertEqual(result["data"], "result")
        self.assertEqual(mock_sleep.call_count, 2)

    def test_auto_retry_all_failures(self):
        """Test _auto_retry returns empty dict when all attempts fail."""
        def always_fails(**kwargs):
            raise ValueError("always fails")

        with mock.patch("time.sleep"):
            result = _auto_retry(always_fails, max_retries=3)

        self.assertEqual(result, {})
        self.assertEqual(result, {})

    def test_auto_retry_immediate_success(self):
        """Test _auto_retry succeeds on first attempt."""
        def immediate_success(**kwargs):
            return {"success": True}

        with mock.patch("time.sleep") as mock_sleep:
            result = _auto_retry(immediate_success, max_retries=3)

        self.assertTrue(result["success"])
        mock_sleep.assert_not_called()

    def test_auto_retry_with_parameters(self):
        """Test _auto_retry passes parameters correctly."""
        def function_with_params(text, language, **kwargs):
            return {"text": f"{text} in {language}", "length": len(text)}

        result = _auto_retry(function_with_params, text="Hello", language="English")

        self.assertEqual(result["text"], "Hello in English")
        self.assertEqual(result["length"], 5)

    def test_translate_title_new_translation(self):
        """Test _translate_title performs new translation correctly."""
        class MockAgent:
            def translate(self, **kwargs):
                return {"text": "你好世界", "tokens": 15, "characters": 8}

        agent = MockAgent()
        metrics = _translate_title(self.entry, target_language="Chinese", engine=agent)

        self.assertEqual(self.entry.translated_title, "你好世界")
        self.assertEqual(metrics["tokens"], 15)
        self.assertEqual(metrics["characters"], 8)

    def test_translate_title_already_translated(self):
        """Test _translate_title skips translation if already done."""
        # First translation
        class MockAgent:
            def translate(self, **kwargs):
                return {"text": "你好世界", "tokens": 15, "characters": 8}

        agent = MockAgent()
        first_metrics = _translate_title(self.entry, target_language="Chinese", engine=agent)
        
        # Second call should skip translation
        second_metrics = _translate_title(self.entry, target_language="Chinese", engine=agent)
        
        self.assertEqual(first_metrics["tokens"], 15)
        self.assertEqual(first_metrics["characters"], 8)
        self.assertEqual(second_metrics["tokens"], 0)
        self.assertEqual(second_metrics["characters"], 0)

    def test_translate_title_with_empty_result(self):
        """Test _translate_title handles empty translation result."""
        class MockAgent:
            def translate(self, **kwargs):
                return {"text": "", "tokens": 0, "characters": 0}

        agent = MockAgent()
        metrics = _translate_title(self.entry, target_language="Chinese", engine=agent)

        self.assertIsNone(self.entry.translated_title)  # Empty string becomes None
        self.assertEqual(metrics["tokens"], 0)
        self.assertEqual(metrics["characters"], 0)

    def test_translate_title_with_missing_metrics(self):
        """Test _translate_title handles missing metrics gracefully."""
        class MockAgent:
            def translate(self, **kwargs):
                return {"text": "你好世界"}  # Missing tokens and characters

        agent = MockAgent()
        metrics = _translate_title(self.entry, target_language="Chinese", engine=agent)

        self.assertEqual(self.entry.translated_title, "你好世界")
        # Should handle missing keys gracefully
        self.assertIn("tokens", metrics)
        self.assertIn("characters", metrics)

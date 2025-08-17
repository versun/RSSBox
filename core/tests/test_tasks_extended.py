from django.test import TestCase
from unittest.mock import patch, Mock, call

from ..models import Feed, Entry
from ..models.agent import TestAgent
from ..tasks import (
    handle_single_feed_fetch,
    handle_feeds_fetch,
    summarize_feed,
    _save_progress,
    _auto_retry,
    _fetch_article_content,
    translate_feed,
)


class TasksExtendedTestCase(TestCase):
    def setUp(self):
        self.feed = Feed.objects.create(
            name="Test Feed",
            feed_url="https://example.com/feed.xml",
            target_language="Chinese Simplified",
        )
        self.agent = TestAgent.objects.create(name="Test Agent")

    @patch("core.tasks.fetch_feed")
    def test_handle_single_feed_fetch_scenarios(self, mock_fetch_feed):
        """Test handle_single_feed_fetch with various scenarios."""
        # Test 1: Feed up to date
        mock_fetch_feed.return_value = {"error": None, "update": False, "feed": None}
        handle_single_feed_fetch(self.feed)
        self.feed.refresh_from_db()
        self.assertTrue(self.feed.fetch_status)
        self.assertIn("Feed is up to date, Skip", self.feed.log)
        
        # Test 2: With etag and max posts
        for i in range(5):
            Entry.objects.create(feed=self.feed, original_title=f"Entry {i}")
        self.feed.max_posts = 5
        self.feed.etag = "existing-etag"
        self.feed.save()
        
        mock_fetch_feed.reset_mock()
        handle_single_feed_fetch(self.feed)
        mock_fetch_feed.assert_called_once_with(
            url=self.feed.feed_url, etag="existing-etag"
        )
        
        # Test 3: Exception handling
        mock_fetch_feed.side_effect = Exception("Network timeout")
        handle_single_feed_fetch(self.feed)
        self.feed.refresh_from_db()
        self.assertFalse(self.feed.fetch_status)
        self.assertIn("Network timeout", self.feed.log)

    @patch("core.tasks.handle_single_feed_fetch")
    def test_handle_feeds_fetch(self, mock_handle_single):
        """Test handle_feeds_fetch with multiple feeds."""
        feed2 = Feed.objects.create(
            name="Feed 2", feed_url="https://example2.com/feed.xml"
        )
        feeds = [self.feed, feed2]

        handle_feeds_fetch(feeds)

        self.assertEqual(mock_handle_single.call_count, 2)

    @patch("core.tasks._auto_retry")
    def test_summarize_feed_functionality(self, mock_auto_retry):
        """Test summarize_feed with various scenarios."""
        # Test 1: No summarizer
        result = summarize_feed(self.feed)
        self.assertFalse(result)
        
        # Test 2: No entries
        self.feed.summarizer = self.agent
        self.feed.save()
        result = summarize_feed(self.feed)
        self.assertFalse(result)
        
        # Test 3: Basic summarization
        entry = Entry.objects.create(
            feed=self.feed,
            original_title="Test Title",
            original_content="<p>This is test content.</p>",
        )
        mock_auto_retry.return_value = {"text": "Summarized content", "tokens": 10}
        result = summarize_feed(self.feed)
        self.assertTrue(result)
        entry.refresh_from_db()
        self.assertEqual(entry.ai_summary, "Summarized content")
        
        # Test 4: Chunking with long content
        long_content = "<p>" + "Long content. " * 100 + "</p>"
        Entry.objects.create(
            feed=self.feed,
            original_title="Long Title",
            original_content=long_content,
        )
        mock_auto_retry.return_value = {"text": "Chunked summary", "tokens": 50}
        result = summarize_feed(self.feed, max_chunk_size=500)
        self.assertIsNotNone(result)
        
        # Test 5: Recursive summarization
        Entry.objects.all().delete()  # Clear previous entries
        for i in range(3):
            Entry.objects.create(
                feed=self.feed,
                original_title=f"Title {i}",
                original_content=f"<p>Content {i} " + "text " * 20 + "</p>",
            )
        mock_auto_retry.reset_mock()
        mock_auto_retry.return_value = {"text": "Recursive summary", "tokens": 25}
        result = summarize_feed(self.feed, summarize_recursively=True, max_chunk_size=200)
        self.assertTrue(result)
        self.assertEqual(mock_auto_retry.call_count, 3)

    def test_save_progress(self):
        """Test _save_progress function."""
        entries = []
        for i in range(3):
            entry = Entry.objects.create(
                feed=self.feed, original_title=f"Title {i}", ai_summary=f"Summary {i}"
            )
            entries.append(entry)

        _save_progress(entries, self.feed, 100)

        self.feed.refresh_from_db()
        self.assertEqual(self.feed.total_tokens, 100)
        for entry in entries:
            entry.refresh_from_db()
            self.assertIsNotNone(entry.ai_summary)

    @patch("core.tasks.logger.error")
    @patch("time.sleep")
    def test_auto_retry_behavior(self, mock_sleep, mock_logging_error):
        """Test _auto_retry with various scenarios."""
        # Test 1: Immediate success
        mock_func = Mock(return_value={"text": "success", "tokens": 10})
        result = _auto_retry(mock_func, max_retries=3, test_arg="value")
        self.assertEqual(result, {"text": "success", "tokens": 10})
        mock_func.assert_called_once_with(test_arg="value")
        mock_sleep.assert_not_called()
        
        # Test 2: Success after retries
        mock_func.reset_mock()
        mock_sleep.reset_mock()
        mock_func.side_effect = [
            Exception("First failure"),
            Exception("Second failure"),
            {"text": "success after retries", "tokens": 15},
        ]
        result = _auto_retry(mock_func, max_retries=3, test_arg="value")
        self.assertEqual(result, {"text": "success after retries", "tokens": 15})
        self.assertEqual(mock_func.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)
        
        # Test 3: Max retries exceeded
        mock_func.reset_mock()
        mock_sleep.reset_mock()
        mock_logging_error.reset_mock()
        mock_func.side_effect = Exception("Persistent failure")
        result = _auto_retry(mock_func, max_retries=2, test_arg="value")
        self.assertEqual(result, {})
        self.assertEqual(mock_func.call_count, 2)
        self.assertEqual(mock_sleep.call_count, 2)
        self.assertEqual(mock_logging_error.call_count, 2)
        mock_logging_error.assert_has_calls([
            call("Attempt 1 failed: Persistent failure"),
            call("Attempt 2 failed: Persistent failure"),
        ])

    @patch("newspaper.Article")
    def test_fetch_article_content(self, mock_article_class):
        """Test _fetch_article_content with success and failure scenarios."""
        # Test 1: Successful fetch
        mock_article = Mock()
        mock_article.text = "Fetched article content"
        mock_article_class.return_value = mock_article
        result = _fetch_article_content("https://example.com/article")
        self.assertEqual(result, "<p>Fetched article content</p>\n")
        mock_article.download.assert_called_once()
        mock_article.parse.assert_called_once()
        
        # Test 2: Exception during download
        mock_article.reset_mock()
        mock_article.download.side_effect = Exception("Download failed")
        result = _fetch_article_content("https://example.com/article")
        self.assertEqual(result, "")

    @patch("core.tasks._fetch_article_content")
    def test_translate_feed_scenarios(self, mock_fetch_article):
        """Test translate_feed with various scenarios."""
        # Test 1: No translator
        Entry.objects.create(feed=self.feed, original_title="Test Title")
        translate_feed(self.feed)
        self.feed.refresh_from_db()
        self.assertFalse(self.feed.translation_status)
        
        # Test 2: No entries
        Entry.objects.all().delete()
        self.feed.translator = self.agent
        self.feed.save()
        translate_feed(self.feed)
        self.feed.refresh_from_db()
        self.assertFalse(self.feed.translation_status)
        
        # Test 3: Exception during article fetching
        self.feed.translate_content = True
        self.feed.fetch_article = True
        self.feed.save()
        Entry.objects.create(
            feed=self.feed,
            original_title="Test Title",
            link="https://example.com/article",
        )
        mock_fetch_article.side_effect = Exception("Fetch failed")
        translate_feed(self.feed, target_field="content")
        # Should handle exception gracefully
        self.feed.refresh_from_db()

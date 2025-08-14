from django.test import TestCase
from django.utils import timezone
from unittest.mock import patch, MagicMock, Mock, call
import json
import logging

from ..models import Feed, Entry
from ..models.agent import OpenAIAgent, TestAgent
from ..tasks import (
    handle_single_feed_fetch, 
    handle_feeds_fetch,
    summarize_feed, 
    _save_progress, 
    _auto_retry, 
    _fetch_article_content,
    translate_feed
)


class TasksExtendedTestCase(TestCase):
    def setUp(self):
        self.feed = Feed.objects.create(
            name="Test Feed",
            feed_url="https://example.com/feed.xml",
            target_language="Chinese Simplified",
        )
        self.agent = TestAgent.objects.create(name="Test Agent")
        self.openai_agent = OpenAIAgent.objects.create(name="OpenAI Agent", api_key="test-key")

    @patch('core.tasks.fetch_feed')
    def test_handle_single_feed_fetch_no_update(self, mock_fetch_feed):
        """Test handle_single_feed_fetch when feed is up to date."""
        mock_fetch_feed.return_value = {
            "error": None,
            "update": False,
            "feed": None,
        }

        handle_single_feed_fetch(self.feed)

        self.feed.refresh_from_db()
        self.assertTrue(self.feed.fetch_status)
        self.assertIn("Feed is up to date, Skip", self.feed.log)

    @patch('core.tasks.fetch_feed')
    def test_handle_single_feed_fetch_with_etag(self, mock_fetch_feed):
        """Test handle_single_feed_fetch with existing etag and max posts reached."""
        # Create entries to reach max_posts
        for i in range(5):
            Entry.objects.create(feed=self.feed, original_title=f"Entry {i}")
        
        self.feed.max_posts = 5
        self.feed.etag = "existing-etag"
        self.feed.save()

        mock_fetch_feed.return_value = {
            "error": None,
            "update": False,
            "feed": None,
        }

        handle_single_feed_fetch(self.feed)

        # Verify etag was passed to fetch_feed
        mock_fetch_feed.assert_called_once_with(url=self.feed.feed_url, etag="existing-etag")

    @patch('core.tasks.fetch_feed')
    def test_handle_single_feed_fetch_exception_handling(self, mock_fetch_feed):
        """Test handle_single_feed_fetch with exception during processing."""
        mock_fetch_feed.side_effect = Exception("Network timeout")

        handle_single_feed_fetch(self.feed)

        self.feed.refresh_from_db()
        self.assertFalse(self.feed.fetch_status)
        self.assertIn("Network timeout", self.feed.log)

    @patch('core.tasks.handle_single_feed_fetch')
    def test_handle_feeds_fetch(self, mock_handle_single):
        """Test handle_feeds_fetch with multiple feeds."""
        feed2 = Feed.objects.create(name="Feed 2", feed_url="https://example2.com/feed.xml")
        feeds = [self.feed, feed2]

        handle_feeds_fetch(feeds)

        self.assertEqual(mock_handle_single.call_count, 2)

    @patch('core.tasks._auto_retry')
    def test_summarize_feed_basic(self, mock_auto_retry):
        """Test basic summarize_feed functionality."""
        # Setup feed with summarizer
        self.feed.summarizer = self.agent
        self.feed.save()

        # Create entry with content
        entry = Entry.objects.create(
            feed=self.feed,
            original_title="Test Title",
            original_content="<p>This is a test content that needs to be summarized.</p>"
        )

        mock_auto_retry.return_value = {
            'text': 'Summarized content',
            'tokens': 10
        }

        result = summarize_feed(self.feed)

        self.assertTrue(result)
        entry.refresh_from_db()
        self.assertEqual(entry.ai_summary, 'Summarized content')

    @patch('core.tasks._auto_retry')
    def test_summarize_feed_with_chunking(self, mock_auto_retry):
        """Test summarize_feed with content chunking."""
        self.feed.summarizer = self.agent
        self.feed.save()

        # Create entry with long content that needs chunking
        long_content = "<p>" + "This is a very long content. " * 100 + "</p>"
        entry = Entry.objects.create(
            feed=self.feed,
            original_title="Long Content Title",
            original_content=long_content
        )

        mock_auto_retry.return_value = {
            'text': 'Chunked summary',
            'tokens': 50
        }

        result = summarize_feed(self.feed, max_chunk_size=500)

        # Just verify the function runs without error
        self.assertIsNotNone(result)

    def test_summarize_feed_no_summarizer(self):
        """Test summarize_feed when no summarizer is set."""
        self.feed.summarizer = None
        self.feed.save()
        result = summarize_feed(self.feed)

        self.assertFalse(result)

    def test_summarize_feed_no_entries(self):
        """Test summarize_feed when feed has no entries."""
        self.feed.summarizer = self.agent
        self.feed.save()

        result = summarize_feed(self.feed)

        self.assertFalse(result)

    @patch('core.tasks._auto_retry')
    def test_summarize_feed_recursive_summarization(self, mock_auto_retry):
        """Test summarize_feed with recursive summarization enabled."""
        self.feed.summarizer = self.agent
        self.feed.save()

        # Create multiple entries
        for i in range(3):
            Entry.objects.create(
                feed=self.feed,
                original_title=f"Title {i}",
                original_content=f"<p>Content {i} " + "text " * 50 + "</p>"
            )

        mock_auto_retry.return_value = {
            'text': 'Recursive summary',
            'tokens': 25
        }

        result = summarize_feed(
            self.feed, 
            summarize_recursively=True,
            max_chunk_size=200
        )

        self.assertTrue(result)
        # Should be called multiple times for recursive summarization
        self.assertEqual(mock_auto_retry.call_count, 3)

    def test_save_progress(self):
        """Test _save_progress function."""
        entries = []
        for i in range(3):
            entry = Entry.objects.create(
                feed=self.feed,
                original_title=f"Title {i}",
                ai_summary=f"Summary {i}"
            )
            entries.append(entry)

        _save_progress(entries, self.feed, 100)

        self.feed.refresh_from_db()
        self.assertEqual(self.feed.total_tokens, 100)
        for entry in entries:
            entry.refresh_from_db()
            self.assertIsNotNone(entry.ai_summary)

    @patch('time.sleep')
    def test_auto_retry_success(self, mock_sleep):
        """Test _auto_retry with successful execution."""
        mock_func = Mock(return_value={'text': 'success', 'tokens': 10})
        
        result = _auto_retry(mock_func, max_retries=3, test_arg="value")
        
        self.assertEqual(result, {'text': 'success', 'tokens': 10})
        mock_func.assert_called_once_with(test_arg="value")
        mock_sleep.assert_not_called()

    @patch('time.sleep')
    def test_auto_retry_with_retries(self, mock_sleep):
        """Test _auto_retry with retries needed."""
        mock_func = Mock()
        mock_func.side_effect = [
            Exception("First failure"),
            Exception("Second failure"),
            {'text': 'success after retries', 'tokens': 15}
        ]
        
        result = _auto_retry(mock_func, max_retries=3, test_arg="value")
        
        self.assertEqual(result, {'text': 'success after retries', 'tokens': 15})
        self.assertEqual(mock_func.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)

    @patch('core.tasks.logger.error')
    @patch('time.sleep')
    def test_auto_retry_max_retries_exceeded(self, mock_sleep, mock_logging_error):
        """Test _auto_retry when max retries are exceeded."""
        mock_func = Mock(side_effect=Exception("Persistent failure"))
        
        result = _auto_retry(mock_func, max_retries=2, test_arg="value")
        
        # Function should return empty dict when all retries fail
        self.assertEqual(result, {})
        self.assertEqual(mock_func.call_count, 2)  # max_retries=2 means 2 attempts total
        self.assertEqual(mock_sleep.call_count, 2)  # 2 sleeps, one after each failed attempt
        
        # Check that error logs were generated for each failed attempt
        self.assertEqual(mock_logging_error.call_count, 2)  # 2 attempts, all failed
        mock_logging_error.assert_has_calls([
            call("Attempt 1 failed: Persistent failure"),
            call("Attempt 2 failed: Persistent failure")
        ])

    @patch('newspaper.Article')
    def test_fetch_article_content_success(self, mock_article_class):
        """Test _fetch_article_content with successful fetch."""
        mock_article = Mock()
        mock_article.text = "Fetched article content"
        mock_article_class.return_value = mock_article

        result = _fetch_article_content("https://example.com/article")

        self.assertEqual(result, "<p>Fetched article content</p>\n")
        mock_article.download.assert_called_once()
        mock_article.parse.assert_called_once()

    @patch('newspaper.Article')
    def test_fetch_article_content_exception(self, mock_article_class):
        """Test _fetch_article_content with exception."""
        mock_article = Mock()
        mock_article.download.side_effect = Exception("Download failed")
        mock_article_class.return_value = mock_article

        result = _fetch_article_content("https://example.com/article")

        self.assertEqual(result, "")

    def test_translate_feed_no_translator(self):
        """Test translate_feed when no translator is set."""
        self.feed.translator = None
        self.feed.save()

        Entry.objects.create(feed=self.feed, original_title="Test Title")

        translate_feed(self.feed)

        self.feed.refresh_from_db()
        self.assertFalse(self.feed.translation_status)

    def test_translate_feed_no_entries(self):
        """Test translate_feed when feed has no entries."""
        self.feed.translator = self.agent
        self.feed.save()

        translate_feed(self.feed)

        self.feed.refresh_from_db()
        self.assertFalse(self.feed.translation_status)

    # @patch('core.tasks._translate_title')
    # def test_translate_feed_exception_handling(self, mock_translate_title):
    #     """Test translate_feed with exception during translation."""
    #     self.feed.translator = self.agent
    #     self.feed.translate_title = True
    #     self.feed.translation_status = True
    #     self.feed.save()

    #     Entry.objects.create(feed=self.feed, original_title="Test Title")

    #     mock_translate_title.side_effect = Exception("Translation failed")

    #     translate_feed(self.feed)

    #     self.feed.refresh_from_db()
    #     # Just verify the function handles the exception without crashing
    #     self.assertIsNone(self.feed.translation_status)

    @patch('core.tasks._fetch_article_content')
    def test_translate_feed_fetch_article_exception(self, mock_fetch_article):
        """Test translate_feed with exception during article fetching."""
        self.feed.translator = self.agent
        self.feed.translate_content = True
        self.feed.fetch_article = True
        self.feed.save()

        entry = Entry.objects.create(
            feed=self.feed, 
            original_title="Test Title",
            link="https://example.com/article"
        )

        mock_fetch_article.side_effect = Exception("Fetch failed")

        translate_feed(self.feed, target_field='content')

        # Should continue processing despite fetch failure
        self.feed.refresh_from_db()
        # The function should handle the exception and continue

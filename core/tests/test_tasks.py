from django.test import TestCase
from django.utils import timezone
from unittest.mock import patch, MagicMock

from ..models import Feed, Entry
from ..models.agent import OpenAIAgent
from ..tasks import (
    handle_single_feed_fetch,
    handle_feeds_fetch,
    handle_feeds_translation,
    handle_feeds_summary,
    _translate_title,
    _translate_content,
    translate_feed,
    _auto_retry,
    _fetch_article_content,
    _save_progress,
    summarize_feed,
)


class TasksTestCase(TestCase):
    def setUp(self):
        self.feed = Feed.objects.create(
            name="Loading",
            feed_url="https://example.com/feed.xml",
        )

    @patch("core.tasks.convert_struct_time_to_datetime")
    @patch("core.tasks.fetch_feed")
    def test_handle_single_feed_fetch_success(self, mock_fetch_feed, mock_convert_time):
        """Test handle_single_feed_fetch with a successful feed fetch."""
        mock_convert_time.return_value = timezone.now()
        mock_feed_data = MagicMock()
        mock_feed_data.feed = {
            "title": "New Test Feed",
            "subtitle": "A subtitle",
            "language": "en",
            "author": "Test Author",
            "link": "https://example.com/home",
            "published_parsed": "mock_time",
            "updated_parsed": "mock_time",
        }

        mock_entry = MagicMock()
        mock_entry.get.side_effect = {
            "id": "guid1",
            "link": "https://example.com/post1",
            "author": "Author1",
            "title": "Title1",
            "summary": "Summary1",
            "published_parsed": "mock_time",
            "updated_parsed": "mock_time",
            "enclosures_xml": None,
        }.get
        mock_entry.content = [MagicMock(value="Content1")]

        mock_feed_data.entries = [mock_entry]
        mock_feed_data.get.return_value = "new-etag"

        mock_fetch_feed.return_value = {
            "error": None,
            "update": True,
            "feed": mock_feed_data,
        }

        handle_single_feed_fetch(self.feed)

        self.feed.refresh_from_db()
        self.assertTrue(self.feed.fetch_status)
        self.assertEqual(self.feed.name, "New Test Feed")
        self.assertEqual(self.feed.etag, "new-etag")
        self.assertEqual(Entry.objects.count(), 1)
        self.assertEqual(Entry.objects.first().original_title, "Title1")

    @patch("core.tasks.fetch_feed")
    def test_handle_single_feed_fetch_error(self, mock_fetch_feed):
        """Test handle_single_feed_fetch with a fetch error."""
        mock_fetch_feed.return_value = {
            "error": "Network Error",
            "update": False,
            "feed": None,
        }

        handle_single_feed_fetch(self.feed)

        self.feed.refresh_from_db()
        self.assertFalse(self.feed.fetch_status)
        self.assertIn("Network Error", self.feed.log)

    @patch("core.tasks.logger.info")
    @patch("core.tasks.translate_feed")
    def test_handle_feeds_translation(self, mock_translate_feed, mock_logger_info):
        """Test the handle_feeds_translation task."""
        Entry.objects.create(feed=self.feed, original_title="An entry to translate")
        feeds = [self.feed]

        handle_feeds_translation(feeds, target_field="title")

        mock_translate_feed.assert_called_once_with(self.feed, target_field="title")
        mock_logger_info.assert_called_once_with(
            "Start translate %s of feed %s to %s",
            "title",
            self.feed.feed_url,
            self.feed.target_language,
        )

        # In the original function, the feeds list is updated in-place.
        # We need to get the updated feed object to check its status.
        updated_feed = Feed.objects.get(id=self.feed.id)
        # translation_status is not set to True in the current implementation (it's commented out)
        # so we check that it's None (set at the beginning) instead
        self.assertIsNone(updated_feed.translation_status)
        self.assertIn("Translate Completed", updated_feed.log)

    def test_handle_feeds_translation_no_entries(self):
        """Test handle_feeds_translation with feeds that have no entries."""
        # This feed has no entries
        feeds = [self.feed]

        handle_feeds_translation(feeds, target_field="title")

        # Feed should not be processed, status should remain unchanged
        updated_feed = Feed.objects.get(id=self.feed.id)
        self.assertIsNone(updated_feed.translation_status)
        # Log should not contain "Translate Completed"
        self.assertNotIn("Translate Completed", updated_feed.log)

    @patch("core.tasks.logger.error")
    @patch("core.tasks.logger.info")
    @patch("core.tasks.translate_feed")
    def test_handle_feeds_translation_error(
        self, mock_translate_feed, mock_logger_info, mock_logger_error
    ):
        """Test handle_feeds_translation when translate_feed raises an exception."""
        Entry.objects.create(feed=self.feed, original_title="An entry to translate")
        mock_translate_feed.side_effect = Exception("Translation failed")
        feeds = [self.feed]

        handle_feeds_translation(feeds, target_field="title")

        mock_translate_feed.assert_called_once_with(self.feed, target_field="title")
        mock_logger_info.assert_called_once_with(
            "Start translate %s of feed %s to %s",
            "title",
            self.feed.feed_url,
            self.feed.target_language,
        )
        mock_logger_error.assert_called_once_with(
            f"Error in translate_feed for feed {self.feed.name}: Translation failed"
        )

        # Check that error status and log are set correctly
        updated_feed = Feed.objects.get(id=self.feed.id)
        self.assertFalse(updated_feed.translation_status)
        self.assertIn("Translation failed", updated_feed.log)

    @patch("core.tasks.logger.info")
    @patch("core.tasks.translate_feed")
    def test_handle_feeds_translation_multiple_feeds(
        self, mock_translate_feed, mock_logger_info
    ):
        """Test handle_feeds_translation with multiple feeds."""
        # Create a second feed
        feed2 = Feed.objects.create(
            name="Second Feed",
            feed_url="https://example.com/feed2.xml",
        )

        # Add entries to both feeds
        Entry.objects.create(feed=self.feed, original_title="Entry 1")
        Entry.objects.create(feed=feed2, original_title="Entry 2")

        feeds = [self.feed, feed2]

        handle_feeds_translation(feeds, target_field="title")

        # Both feeds should be called
        self.assertEqual(mock_translate_feed.call_count, 2)
        mock_translate_feed.assert_any_call(self.feed, target_field="title")
        mock_translate_feed.assert_any_call(feed2, target_field="title")

        # Both feeds should have logger calls
        self.assertEqual(mock_logger_info.call_count, 2)

        # Check both feeds are updated
        updated_feed1 = Feed.objects.get(id=self.feed.id)
        updated_feed2 = Feed.objects.get(id=feed2.id)
        self.assertIsNone(updated_feed1.translation_status)
        self.assertIsNone(updated_feed2.translation_status)
        self.assertIn("Translate Completed", updated_feed1.log)
        self.assertIn("Translate Completed", updated_feed2.log)

    @patch("core.tasks.logger.info")
    @patch("core.tasks.translate_feed")
    def test_handle_feeds_translation_content_field(
        self, mock_translate_feed, mock_logger_info
    ):
        """Test handle_feeds_translation with target_field='content'."""
        Entry.objects.create(feed=self.feed, original_title="An entry to translate")
        feeds = [self.feed]

        handle_feeds_translation(feeds, target_field="content")

        mock_translate_feed.assert_called_once_with(self.feed, target_field="content")
        mock_logger_info.assert_called_once_with(
            "Start translate %s of feed %s to %s",
            "content",
            self.feed.feed_url,
            self.feed.target_language,
        )

        updated_feed = Feed.objects.get(id=self.feed.id)
        self.assertIsNone(updated_feed.translation_status)
        self.assertIn("Translate Completed", updated_feed.log)

    @patch("core.tasks.summarize_feed")
    def test_handle_feeds_summary_success(self, mock_summarize_feed):
        """Test the handle_feeds_summary task with a successful summarization."""
        Entry.objects.create(feed=self.feed, original_title="An entry to summarize")
        # Mock a summarizer agent for the feed
        self.feed.summarizer = OpenAIAgent.objects.create(
            name="Summarizer Agent", api_key="key"
        )
        self.feed.save()
        feeds = [self.feed]

        handle_feeds_summary(feeds)

        mock_summarize_feed.assert_called_once()
        updated_feed = Feed.objects.get(id=self.feed.id)
        self.assertTrue(updated_feed.translation_status)
        self.assertIn("Summary Completed", updated_feed.log)

    def test_handle_feeds_summary_no_summarizer(self):
        """Test handle_feeds_summary when no summarizer is set."""
        Entry.objects.create(feed=self.feed, original_title="An entry to summarize")
        self.feed.summarizer = None
        self.feed.save()
        feeds = [self.feed]

        handle_feeds_summary(feeds)

        updated_feed = Feed.objects.get(id=self.feed.id)
        self.assertFalse(updated_feed.translation_status)
        self.assertIn("Summarizer Engine Not Set", updated_feed.log)


class TasksHelperFunctionsTest(TestCase):
    def setUp(self):
        self.agent = OpenAIAgent.objects.create(name="Test Agent", api_key="key")
        self.feed = Feed.objects.create(name="Test Feed")
        self.entry = Entry.objects.create(
            feed=self.feed,
            original_title="Original Title",
            original_content="<p>Original Content</p>",
        )

    def test_translate_title_needed(self):
        """Test _translate_title when translation is needed."""
        with patch.object(
            self.agent,
            "translate",
            return_value={"text": "Translated Title", "tokens": 5},
        ) as mock_translate:
            result = _translate_title(self.entry, "en", self.agent)
            mock_translate.assert_called_once_with(
                text="Original Title", target_language="en", text_type="title"
            )
            self.assertEqual(self.entry.translated_title, "Translated Title")
            self.assertEqual(result["tokens"], 5)

    def test_translate_title_not_needed(self):
        """Test _translate_title when translation is not needed."""
        self.entry.translated_title = "Already Translated"
        self.entry.save()
        with patch.object(self.agent, "translate") as mock_translate:
            result = _translate_title(self.entry, "en", self.agent)
            mock_translate.assert_not_called()
            self.assertEqual(result["tokens"], 0)

    @patch("core.tasks._auto_retry")
    def test_translate_content_needed(self, mock_auto_retry):
        """Test _translate_content when translation is needed."""
        self.entry.original_content = "<p>Original Content</p>"
        self.entry.save()
        mock_auto_retry.return_value = {
            "text": "<p>Translated Content</p>",
            "tokens": 10,
        }

        result = _translate_content(self.entry, "en", self.agent)

        mock_auto_retry.assert_called_once()
        self.assertEqual(self.entry.translated_content, "<p>Translated Content</p>")
        self.assertEqual(result["tokens"], 10)

    @patch("core.tasks._auto_retry")
    def test_translate_content_not_needed(self, mock_auto_retry):
        """Test _translate_content when translation is not needed."""
        self.entry.original_content = "<p>Original Content</p>"
        self.entry.translated_content = "<p>Already Translated</p>"
        self.entry.save()

        result = _translate_content(self.entry, "en", self.agent)

        mock_auto_retry.assert_not_called()
        self.assertEqual(result["tokens"], 0)

    @patch("core.tasks._translate_title")
    def test_translate_feed_for_title(self, mock_translate_title):
        """Test translate_feed for title translation."""
        self.feed.translator = self.agent
        self.feed.translate_title = True
        self.feed.save()

        mock_translate_title.return_value = {"tokens": 10, "characters": 50}

        translate_feed(self.feed, target_field="title")

        mock_translate_title.assert_called_once_with(
            entry=self.entry,
            target_language=self.feed.target_language,
            engine=self.agent,
        )
        self.assertEqual(self.feed.total_tokens, 10)
        self.assertEqual(self.feed.total_characters, 50)

    @patch("core.tasks._fetch_article_content")
    @patch("core.tasks._translate_content")
    def test_translate_feed_for_content(
        self, mock_translate_content, mock_fetch_article
    ):
        """Test translate_feed for content translation."""
        self.feed.translator = self.agent
        self.feed.translate_content = True
        self.feed.fetch_article = False
        self.feed.save()

        mock_translate_content.return_value = {"tokens": 100, "characters": 500}

        translate_feed(self.feed, target_field="content")

        mock_fetch_article.assert_not_called()
        mock_translate_content.assert_called_once_with(
            entry=self.entry,
            target_language=self.feed.target_language,
            engine=self.agent,
        )
        self.assertEqual(self.feed.total_tokens, 100)
        self.assertEqual(self.feed.total_characters, 500)

    @patch("core.tasks._fetch_article_content")
    @patch("core.tasks._translate_content")
    def test_translate_feed_with_fetch_article(
        self, mock_translate_content, mock_fetch_article
    ):
        """Test translate_feed with fetch_article enabled."""
        self.feed.translator = self.agent
        self.feed.translate_content = True
        self.feed.fetch_article = True
        self.feed.save()

        mock_fetch_article.return_value = "<p>Fetched Article Content</p>"
        mock_translate_content.return_value = {"tokens": 150, "characters": 750}

        translate_feed(self.feed, target_field="content")

        mock_fetch_article.assert_called_once_with(self.entry.link)
        mock_translate_content.assert_called_once()

        # Check the arguments passed to the mock
        call_args, call_kwargs = mock_translate_content.call_args
        updated_entry = call_kwargs.get("entry")
        self.assertIsNotNone(updated_entry)
        self.assertEqual(
            updated_entry.original_content, "<p>Fetched Article Content</p>"
        )
        self.assertEqual(call_kwargs.get("target_language"), self.feed.target_language)
        self.assertEqual(call_kwargs.get("engine"), self.agent)
        self.assertEqual(self.feed.total_tokens, 150)
        self.assertEqual(self.feed.total_characters, 750)

    @patch("core.tasks.fetch_feed")
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

    def test_handle_feeds_fetch(self):
        """Test handle_feeds_fetch function."""
        feed1 = Feed.objects.create(name="Feed1", feed_url="https://example.com/feed1.xml")
        feed2 = Feed.objects.create(name="Feed2", feed_url="https://example.com/feed2.xml")
        
        with patch("core.tasks.handle_single_feed_fetch") as mock_handle:
            handle_feeds_fetch([feed1, feed2])
            self.assertEqual(mock_handle.call_count, 2)

    def test_auto_retry_success(self):
        """Test _auto_retry function with successful call."""
        mock_func = MagicMock(return_value={"text": "success", "tokens": 10})
        
        result = _auto_retry(mock_func, max_retries=3, text="test")
        
        self.assertEqual(result, {"text": "success", "tokens": 10})
        mock_func.assert_called_once_with(text="test")

    def test_auto_retry_with_failures(self):
        """Test _auto_retry function with failures then success."""
        mock_func = MagicMock()
        mock_func.side_effect = [Exception("Error 1"), Exception("Error 2"), {"text": "success"}]
        
        with patch("core.tasks.time.sleep"):
            result = _auto_retry(mock_func, max_retries=3, text="test")
        
        self.assertEqual(result, {"text": "success"})
        self.assertEqual(mock_func.call_count, 3)

    def test_auto_retry_all_failures(self):
        """Test _auto_retry function when all attempts fail."""
        mock_func = MagicMock(side_effect=Exception("Always fails"))
        
        with patch("core.tasks.time.sleep"):
            result = _auto_retry(mock_func, max_retries=2, text="test")
        
        self.assertEqual(result, {})
        self.assertEqual(mock_func.call_count, 2)

    @patch("core.tasks.newspaper.Article")
    def test_fetch_article_content_success(self, mock_article_class):
        """Test _fetch_article_content with successful fetch."""
        mock_article = MagicMock()
        mock_article.text = "Article content"
        mock_article_class.return_value = mock_article
        
        with patch("core.tasks.mistune.html", return_value="<p>Article content</p>"):
            result = _fetch_article_content("https://example.com/article")
        
        self.assertEqual(result, "<p>Article content</p>")
        mock_article.download.assert_called_once()
        mock_article.parse.assert_called_once()

    @patch("core.tasks.newspaper.Article")
    def test_fetch_article_content_failure(self, mock_article_class):
        """Test _fetch_article_content with fetch failure."""
        mock_article_class.side_effect = Exception("Network error")
        
        result = _fetch_article_content("https://example.com/article")
        
        self.assertEqual(result, "")

    def test_save_progress_with_entries(self):
        """Test _save_progress function with entries to save."""
        entry1 = Entry.objects.create(feed=self.feed, original_title="Title1")
        entry2 = Entry.objects.create(feed=self.feed, original_title="Title2")
        entries_to_save = [entry1, entry2]
        
        _save_progress(entries_to_save, self.feed, 100)
        
        self.feed.refresh_from_db()
        self.assertEqual(self.feed.total_tokens, 100)

    def test_save_progress_empty_entries(self):
        """Test _save_progress function with empty entries list."""
        initial_tokens = self.feed.total_tokens
        
        _save_progress([], self.feed, 50)
        
        self.feed.refresh_from_db()
        self.assertEqual(self.feed.total_tokens, initial_tokens + 50)


    def test_handle_feeds_summary_no_entries(self):
        """Test handle_feeds_summary when feed has no entries."""
        # Feed has no entries
        feeds = [self.feed]

        handle_feeds_summary(feeds)

        # Feed should not be processed, but translation_status is set to None initially then bulk_update sets it
        updated_feed = Feed.objects.get(id=self.feed.id)
        # The function continues and sets translation_status in bulk_update even for feeds with no entries
        self.assertFalse(updated_feed.translation_status)

    @patch("core.tasks.translate_feed")
    def test_translate_feed_no_translator(self, mock_translate_feed):
        """Test translate_feed when no translator is set."""
        Entry.objects.create(feed=self.feed, original_title="Test title")
        self.feed.translator = None
        self.feed.translate_title = True
        self.feed.save()

        # This should raise an exception internally
        translate_feed(self.feed, target_field="title")

        # The function should handle the exception gracefully
        # Check that the feed's translation_status is set to False
        self.feed.refresh_from_db()
        self.assertFalse(self.feed.translation_status)

    def test_auto_retry_large_text_cleanup(self):
        """Test _auto_retry function with large text cleanup."""
        mock_func = MagicMock(return_value={"text": "success"})
        large_text = "x" * 2000  # Large text > 1000 chars
        
        result = _auto_retry(mock_func, max_retries=1, text=large_text, other_param="small")
        
        self.assertEqual(result, {"text": "success"})
        mock_func.assert_called_once_with(text=large_text, other_param="small")


    @patch("core.tasks.text_handler")
    def test_summarize_feed_basic(self, mock_text_handler):
        """Test summarize_feed function with basic functionality."""
        # Create entries for summarization
        entry1 = Entry.objects.create(
            feed=self.feed,
            original_title="Title 1",
            original_content="Content for entry 1"
        )
        entry2 = Entry.objects.create(
            feed=self.feed,
            original_title="Title 2", 
            original_content="Content for entry 2"
        )
        
        # Set up summarizer
        self.feed.summarizer = self.agent
        self.feed.summary_detail = 0.5
        self.feed.save()
        
        # Mock text_handler functions
        mock_text_handler.clean_content.side_effect = lambda x: x
        mock_text_handler.get_token_count.return_value = 100
        mock_text_handler.adaptive_chunking.return_value = ["Chunk 1"]
        
        # Mock agent summarize method
        with patch.object(self.agent, 'summarize') as mock_summarize:
            mock_summarize.return_value = {"text": "Summary text", "tokens": 20}
            
            result = summarize_feed(self.feed)
            
        self.assertTrue(result)
        # Check that entries were updated
        entry1.refresh_from_db()
        entry2.refresh_from_db()
        self.assertEqual(entry1.ai_summary, "Summary text")
        self.assertEqual(entry2.ai_summary, "Summary text")

    @patch("core.tasks.text_handler")
    def test_summarize_feed_empty_content(self, mock_text_handler):
        """Test summarize_feed with empty content."""
        entry = Entry.objects.create(
            feed=self.feed,
            original_title="Empty Entry",
            original_content=""
        )
        
        self.feed.summarizer = self.agent
        self.feed.save()
        
        mock_text_handler.clean_content.return_value = ""
        
        result = summarize_feed(self.feed)
        
        self.assertTrue(result)
        entry.refresh_from_db()
        self.assertEqual(entry.ai_summary, "[No content available]")


from django.test import TestCase
from django.utils import timezone
from unittest.mock import patch, MagicMock

from ..models import Feed, Entry
from ..models.agent import OpenAIAgent
from ..tasks import handle_single_feed_fetch, handle_feeds_translation, handle_feeds_summary, _translate_title, _translate_content, translate_feed


class TasksTestCase(TestCase):
    def setUp(self):
        self.feed = Feed.objects.create(
            name="Loading",
            feed_url="https://example.com/feed.xml",
        )

    @patch('core.tasks.convert_struct_time_to_datetime')
    @patch('core.tasks.fetch_feed')
    def test_handle_single_feed_fetch_success(self, mock_fetch_feed, mock_convert_time):
        """Test handle_single_feed_fetch with a successful feed fetch."""
        mock_convert_time.return_value = timezone.now()
        mock_feed_data = MagicMock()
        mock_feed_data.feed = {
            'title': 'New Test Feed',
            'subtitle': 'A subtitle',
            'language': 'en',
            'author': 'Test Author',
            'link': 'https://example.com/home',
            'published_parsed': 'mock_time',
            'updated_parsed': 'mock_time',
        }

        mock_entry = MagicMock()
        mock_entry.get.side_effect = {
            'id': 'guid1',
            'link': 'https://example.com/post1',
            'author': 'Author1',
            'title': 'Title1',
            'summary': 'Summary1',
            'published_parsed': 'mock_time',
            'updated_parsed': 'mock_time',
            'enclosures_xml': None,
        }.get
        mock_entry.content = [MagicMock(value='Content1')]

        mock_feed_data.entries = [mock_entry]
        mock_feed_data.get.return_value = 'new-etag'

        mock_fetch_feed.return_value = {
            "error": None,
            "update": True,
            "feed": mock_feed_data,
        }

        handle_single_feed_fetch(self.feed)

        self.feed.refresh_from_db()
        self.assertTrue(self.feed.fetch_status)
        self.assertEqual(self.feed.name, 'New Test Feed')
        self.assertEqual(self.feed.etag, 'new-etag')
        self.assertEqual(Entry.objects.count(), 1)
        self.assertEqual(Entry.objects.first().original_title, 'Title1')

    @patch('core.tasks.fetch_feed')
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

    @patch('core.tasks.translate_feed')
    def test_handle_feeds_translation(self, mock_translate_feed):
        """Test the handle_feeds_translation task."""
        Entry.objects.create(feed=self.feed, original_title="An entry to translate")
        feeds = [self.feed]
        
        handle_feeds_translation(feeds, target_field='title')

        mock_translate_feed.assert_called_once_with(self.feed, target_field='title')
        
        # In the original function, the feeds list is updated in-place.
        # We need to get the updated feed object to check its status.
        updated_feed = Feed.objects.get(id=self.feed.id)
        self.assertTrue(updated_feed.translation_status)
        self.assertIn("Translate Completed", updated_feed.log)

    @patch('core.tasks.summarize_feed')
    def test_handle_feeds_summary_success(self, mock_summarize_feed):
        """Test the handle_feeds_summary task with a successful summarization."""
        Entry.objects.create(feed=self.feed, original_title="An entry to summarize")
        # Mock a summarizer agent for the feed
        self.feed.summarizer = OpenAIAgent.objects.create(name="Summarizer Agent", api_key="key")
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
        self.entry = Entry.objects.create(feed=self.feed, original_title="Original Title", original_content="<p>Original Content</p>")

    def test_translate_title_needed(self):
        """Test _translate_title when translation is needed."""
        with patch.object(self.agent, 'translate', return_value={'text': 'Translated Title', 'tokens': 5}) as mock_translate:
            result = _translate_title(self.entry, 'en', self.agent)
            mock_translate.assert_called_once_with(text='Original Title', target_language='en', text_type='title')
            self.assertEqual(self.entry.translated_title, 'Translated Title')
            self.assertEqual(result['tokens'], 5)

    def test_translate_title_not_needed(self):
        """Test _translate_title when translation is not needed."""
        self.entry.translated_title = "Already Translated"
        self.entry.save()
        with patch.object(self.agent, 'translate') as mock_translate:
            result = _translate_title(self.entry, 'en', self.agent)
            mock_translate.assert_not_called()
            self.assertEqual(result['tokens'], 0)

    @patch('core.tasks._auto_retry')
    def test_translate_content_needed(self, mock_auto_retry):
        """Test _translate_content when translation is needed."""
        self.entry.original_content = "<p>Original Content</p>"
        self.entry.save()
        mock_auto_retry.return_value = {'text': '<p>Translated Content</p>', 'tokens': 10}

        result = _translate_content(self.entry, 'en', self.agent)

        mock_auto_retry.assert_called_once()
        self.assertEqual(self.entry.translated_content, '<p>Translated Content</p>')
        self.assertEqual(result['tokens'], 10)

    @patch('core.tasks._auto_retry')
    def test_translate_content_not_needed(self, mock_auto_retry):
        """Test _translate_content when translation is not needed."""
        self.entry.original_content = "<p>Original Content</p>"
        self.entry.translated_content = "<p>Already Translated</p>"
        self.entry.save()

        result = _translate_content(self.entry, 'en', self.agent)

        mock_auto_retry.assert_not_called()
        self.assertEqual(result['tokens'], 0)

    @patch('core.tasks._translate_title')
    def test_translate_feed_for_title(self, mock_translate_title):
        """Test translate_feed for title translation."""
        self.feed.translator = self.agent
        self.feed.translate_title = True
        self.feed.save()

        mock_translate_title.return_value = {'tokens': 10, 'characters': 50}

        translate_feed(self.feed, target_field='title')

        mock_translate_title.assert_called_once_with(entry=self.entry, target_language=self.feed.target_language, engine=self.agent)
        self.assertEqual(self.feed.total_tokens, 10)
        self.assertEqual(self.feed.total_characters, 50)

    @patch('core.tasks._fetch_article_content')
    @patch('core.tasks._translate_content')
    def test_translate_feed_for_content(self, mock_translate_content, mock_fetch_article):
        """Test translate_feed for content translation."""
        self.feed.translator = self.agent
        self.feed.translate_content = True
        self.feed.fetch_article = False
        self.feed.save()

        mock_translate_content.return_value = {'tokens': 100, 'characters': 500}

        translate_feed(self.feed, target_field='content')

        mock_fetch_article.assert_not_called()
        mock_translate_content.assert_called_once_with(entry=self.entry, target_language=self.feed.target_language, engine=self.agent)
        self.assertEqual(self.feed.total_tokens, 100)
        self.assertEqual(self.feed.total_characters, 500)

    @patch('core.tasks._fetch_article_content')
    @patch('core.tasks._translate_content')
    def test_translate_feed_with_fetch_article(self, mock_translate_content, mock_fetch_article):
        """Test translate_feed with fetch_article enabled."""
        self.feed.translator = self.agent
        self.feed.translate_content = True
        self.feed.fetch_article = True
        self.feed.save()

        mock_fetch_article.return_value = "<p>Fetched Article Content</p>"
        mock_translate_content.return_value = {'tokens': 150, 'characters': 750}

        translate_feed(self.feed, target_field='content')

        mock_fetch_article.assert_called_once_with(self.entry.link)
        mock_translate_content.assert_called_once()

        # Check the arguments passed to the mock
        call_args, call_kwargs = mock_translate_content.call_args
        updated_entry = call_kwargs.get('entry')
        self.assertIsNotNone(updated_entry)
        self.assertEqual(updated_entry.original_content, "<p>Fetched Article Content</p>")
        self.assertEqual(call_kwargs.get('target_language'), self.feed.target_language)
        self.assertEqual(call_kwargs.get('engine'), self.agent)
        self.assertEqual(self.feed.total_tokens, 150)
        self.assertEqual(self.feed.total_characters, 750)

from django.test import TestCase
from django.test.client import RequestFactory
from django.contrib.admin import ModelAdmin
from django.contrib.messages.storage.fallback import FallbackStorage
from lxml import etree

from ..models import Feed, Entry, Tag
from ..actions import clean_translated_content, _generate_opml_feed, clean_ai_summary, feed_force_update, tag_force_update, feed_batch_modify
from unittest.mock import patch


class ActionsTestCase(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.feed = Feed.objects.create(name="Test Feed for Action", feed_url="https://example.com/rss.xml")
        self.entry1 = Entry.objects.create(
            feed=self.feed,
            original_title="Title 1",
            translated_title="Translated Title 1",
            translated_content="Translated Content 1"
        )
        self.entry2 = Entry.objects.create(
            feed=self.feed,
            original_title="Title 2",
            translated_title="Translated Title 2",
            translated_content="Translated Content 2"
        )
        # Mock ModelAdmin
        self.modeladmin = ModelAdmin(Feed, None)
        self.entry1.ai_summary = "This is an AI summary."
        self.entry1.save()

    def test_clean_translated_content_action(self):
        """Test the clean_translated_content admin action."""
        request = self.factory.get('/')
        # Mock messages framework
        setattr(request, 'session', 'session')
        messages = FallbackStorage(request)
        setattr(request, '_messages', messages)

        queryset = Feed.objects.filter(id=self.feed.id)

        clean_translated_content(self.modeladmin, request, queryset)

        self.entry1.refresh_from_db()
        self.entry2.refresh_from_db()

        self.assertIsNone(self.entry1.translated_title)
        self.assertIsNone(self.entry1.translated_content)
        self.assertIsNone(self.entry2.translated_title)
        self.assertIsNone(self.entry2.translated_content)

    def test_generate_opml_feed(self):
        """Test the _generate_opml_feed helper function."""
        tag = Tag.objects.create(name="Tech")
        self.feed.tags.add(tag)

        queryset = Feed.objects.filter(id=self.feed.id)
        get_url_func = lambda feed: feed.feed_url

        response = _generate_opml_feed("Test Export", queryset, get_url_func, "test")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/xml')
        self.assertIn('attachment; filename="test_feeds_from_rsstranslator.opml"', response['Content-Disposition'])

        # Parse and validate XML content
        root = etree.fromstring(response.content)
        self.assertEqual(root.tag, 'opml')
        self.assertEqual(root.find('head/title').text, "Test Export | RSS Translator")
        category_outline = root.find('body/outline[@title="Tech"]')
        self.assertIsNotNone(category_outline)
        feed_outline = category_outline.find('outline')
        self.assertIsNotNone(feed_outline)
        self.assertEqual(feed_outline.get('title'), self.feed.name)
        self.assertEqual(feed_outline.get('xmlUrl'), self.feed.feed_url)

    def test_clean_ai_summary_action(self):
        """Test the clean_ai_summary admin action."""
        request = self.factory.get('/')
        setattr(request, 'session', 'session')
        messages = FallbackStorage(request)
        setattr(request, '_messages', messages)

        queryset = Feed.objects.filter(id=self.feed.id)

        clean_ai_summary(self.modeladmin, request, queryset)

        self.entry1.refresh_from_db()
        self.assertIsNone(self.entry1.ai_summary)

    @patch('core.actions.task_manager.submit_task')
    def test_feed_force_update_action(self, mock_submit_task):
        """Test the feed_force_update admin action."""
        request = self.factory.get('/')
        queryset = Feed.objects.filter(id=self.feed.id)

        feed_force_update(self.modeladmin, request, queryset)

        self.feed.refresh_from_db()
        self.assertIsNone(self.feed.fetch_status)
        self.assertIsNone(self.feed.translation_status)
        mock_submit_task.assert_called_once()

    @patch('core.actions.task_manager.submit_task')
    def test_tag_force_update_action(self, mock_submit_task):
        """Test the tag_force_update admin action."""
        request = self.factory.get('/')
        tag = Tag.objects.create(name="Test Tag")
        queryset = Tag.objects.filter(id=tag.id)

        tag_force_update(self.modeladmin, request, queryset)

        tag.refresh_from_db()
        self.assertIsNotNone(tag.last_updated)
        self.assertEqual(mock_submit_task.call_count, 2)

    def test_feed_batch_modify_boolean_fields(self):
        """Test the feed_batch_modify action for boolean fields."""
        post_data = {
            'apply': 'Apply',
            'translate_title': 'True',
            'summary': 'False'
        }
        request = self.factory.post('/', post_data)
        queryset = Feed.objects.filter(id=self.feed.id)

        response = feed_batch_modify(self.modeladmin, request, queryset)

        self.assertEqual(response.status_code, 302) # Should redirect after apply
        self.feed.refresh_from_db()
        self.assertTrue(self.feed.translate_title)
        self.assertFalse(self.feed.summary)

    @patch('core.actions.get_all_agent_choices', return_value=[])
    @patch('core.actions.get_ai_agent_choices', return_value=[])
    def test_feed_batch_modify_other_fields(self, mock_ai_agents, mock_all_agents):
        """Test the feed_batch_modify action for other field types."""
        tag = Tag.objects.create(name="New Tag")
        post_data = {
            'apply': 'Apply',
            'update_frequency': 'Change',
            'update_frequency_value': '60',
            'tags': 'Change',
            'tags_value': [str(tag.id)]
        }
        request = self.factory.post('/', post_data)
        queryset = Feed.objects.filter(id=self.feed.id)

        response = feed_batch_modify(self.modeladmin, request, queryset)

        self.assertEqual(response.status_code, 302)
        self.feed.refresh_from_db()
        self.assertEqual(self.feed.update_frequency, 60)
        self.assertIn(tag, self.feed.tags.all())

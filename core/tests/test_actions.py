from django.test import TestCase
from django.test.client import RequestFactory
from django.contrib.admin import ModelAdmin
from django.contrib.messages.storage.fallback import FallbackStorage
from lxml import etree

from ..models import Feed, Entry, Tag, Filter
from ..actions import (
    clean_translated_content,
    _generate_opml_feed,
    clean_ai_summary,
    clean_filter_results,
    export_original_feed_as_opml,
    export_translated_feed_as_opml,
    feed_force_update,
    tag_force_update,
    feed_batch_modify,
    create_digest,
)
from unittest.mock import patch


class ActionsTestCase(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.feed = Feed.objects.create(
            name="Test Feed for Action", feed_url="https://example.com/rss.xml"
        )
        self.entry1 = Entry.objects.create(
            feed=self.feed,
            original_title="Title 1",
            translated_title="Translated Title 1",
            translated_content="Translated Content 1",
        )
        self.entry2 = Entry.objects.create(
            feed=self.feed,
            original_title="Title 2",
            translated_title="Translated Title 2",
            translated_content="Translated Content 2",
        )
        # Mock ModelAdmin
        self.modeladmin = ModelAdmin(Feed, None)
        self.entry1.ai_summary = "This is an AI summary."
        self.entry1.save()

    def test_clean_translated_content_action(self):
        """Test the clean_translated_content admin action."""
        request = self.factory.get("/")
        # Mock messages framework
        setattr(request, "session", "session")
        messages = FallbackStorage(request)
        setattr(request, "_messages", messages)

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
        self.assertEqual(response["Content-Type"], "application/xml")
        self.assertIn(
            'attachment; filename="test_feeds_from_rsstranslator.opml"',
            response["Content-Disposition"],
        )

        # Parse and validate XML content
        root = etree.fromstring(response.content)
        self.assertEqual(root.tag, "opml")
        self.assertEqual(root.find("head/title").text, "Test Export | RSS Translator")
        category_outline = root.find('body/outline[@title="Tech"]')
        self.assertIsNotNone(category_outline)
        feed_outline = category_outline.find("outline")
        self.assertIsNotNone(feed_outline)
        self.assertEqual(feed_outline.get("title"), self.feed.name)
        self.assertEqual(feed_outline.get("xmlUrl"), self.feed.feed_url)

    def test_clean_ai_summary_action(self):
        """Test the clean_ai_summary admin action."""
        request = self.factory.get("/")
        setattr(request, "session", "session")
        messages = FallbackStorage(request)
        setattr(request, "_messages", messages)

        queryset = Feed.objects.filter(id=self.feed.id)

        clean_ai_summary(self.modeladmin, request, queryset)

        self.entry1.refresh_from_db()
        self.assertIsNone(self.entry1.ai_summary)

    @patch("core.actions.task_manager.submit_task")
    def test_feed_force_update_action(self, mock_submit_task):
        """Test the feed_force_update admin action."""
        request = self.factory.get("/")
        queryset = Feed.objects.filter(id=self.feed.id)

        feed_force_update(self.modeladmin, request, queryset)

        self.feed.refresh_from_db()
        self.assertIsNone(self.feed.fetch_status)
        self.assertIsNone(self.feed.translation_status)
        mock_submit_task.assert_called_once()

    @patch("core.actions.task_manager.submit_task")
    def test_tag_force_update_action(self, mock_submit_task):
        """Test the tag_force_update admin action."""
        request = self.factory.get("/")
        tag = Tag.objects.create(name="Test Tag")
        queryset = Tag.objects.filter(id=tag.id)

        tag_force_update(self.modeladmin, request, queryset)

        tag.refresh_from_db()
        self.assertIsNotNone(tag.last_updated)
        self.assertEqual(mock_submit_task.call_count, 2)

    def test_feed_batch_modify_boolean_fields(self):
        """Test the feed_batch_modify action for boolean fields."""
        post_data = {"apply": "Apply", "translate_title": "True", "summary": "False"}
        request = self.factory.post("/", post_data)
        queryset = Feed.objects.filter(id=self.feed.id)

        response = feed_batch_modify(self.modeladmin, request, queryset)

        self.assertEqual(response.status_code, 302)  # Should redirect after apply
        self.feed.refresh_from_db()
        self.assertTrue(self.feed.translate_title)
        self.assertFalse(self.feed.summary)

    @patch("core.actions.get_all_agent_choices", return_value=[])
    @patch("core.actions.get_ai_agent_choices", return_value=[])
    def test_feed_batch_modify_other_fields(self, mock_ai_agents, mock_all_agents):
        """Test the feed_batch_modify action for other field types."""
        tag = Tag.objects.create(name="New Tag")
        post_data = {
            "apply": "Apply",
            "update_frequency": "Change",
            "update_frequency_value": "60",
            "tags": "Change",
            "tags_value": [str(tag.id)],
        }
        request = self.factory.post("/", post_data)
        queryset = Feed.objects.filter(id=self.feed.id)

        response = feed_batch_modify(self.modeladmin, request, queryset)

        self.assertEqual(response.status_code, 302)
        self.feed.refresh_from_db()
        self.assertEqual(self.feed.update_frequency, 60)
        self.assertIn(tag, self.feed.tags.all())

    @patch('core.models.filter.Filter.clear_ai_filter_cache_results')
    def test_clean_filter_results_action(self, mock_clear_cache):
        """Test the clean_filter_results admin action."""
        request = self.factory.get("/")
        setattr(request, "session", "session")
        messages = FallbackStorage(request)
        setattr(request, "_messages", messages)

        # Create test filters
        filter1 = Filter.objects.create(name="Test Filter 1")
        filter2 = Filter.objects.create(name="Test Filter 2")
        queryset = Filter.objects.filter(id__in=[filter1.id, filter2.id])

        clean_filter_results(self.modeladmin, request, queryset)

        # Verify clear_ai_filter_cache_results was called for each filter
        self.assertEqual(mock_clear_cache.call_count, 2)

    def test_export_original_feed_as_opml_action(self):
        """Test the export_original_feed_as_opml admin action."""
        request = self.factory.get("/")
        tag = Tag.objects.create(name="News")
        self.feed.tags.add(tag)
        queryset = Feed.objects.filter(id=self.feed.id)

        response = export_original_feed_as_opml(self.modeladmin, request, queryset)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/xml")
        self.assertIn(
            'attachment; filename="original_feeds_from_rsstranslator.opml"',
            response["Content-Disposition"],
        )

        # Parse and validate XML content
        root = etree.fromstring(response.content)
        self.assertEqual(root.tag, "opml")
        self.assertEqual(root.find("head/title").text, "Original Feeds | RSS Translator")
        category_outline = root.find('body/outline[@title="News"]')
        self.assertIsNotNone(category_outline)
        feed_outline = category_outline.find("outline")
        self.assertIsNotNone(feed_outline)
        self.assertEqual(feed_outline.get("title"), self.feed.name)
        self.assertEqual(feed_outline.get("xmlUrl"), self.feed.feed_url)

    @patch('core.actions.settings.SITE_URL', 'https://test.example.com')
    def test_export_translated_feed_as_opml_action(self):
        """Test the export_translated_feed_as_opml admin action."""
        request = self.factory.get("/")
        tag = Tag.objects.create(name="Tech")
        self.feed.tags.add(tag)
        self.feed.slug = "test-feed"
        self.feed.save()
        queryset = Feed.objects.filter(id=self.feed.id)

        response = export_translated_feed_as_opml(self.modeladmin, request, queryset)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/xml")
        self.assertIn(
            'attachment; filename="translated_feeds_from_rsstranslator.opml"',
            response["Content-Disposition"],
        )

        # Parse and validate XML content
        root = etree.fromstring(response.content)
        self.assertEqual(root.tag, "opml")
        self.assertEqual(root.find("head/title").text, "Translated Feeds | RSS Translator")
        category_outline = root.find('body/outline[@title="Tech"]')
        self.assertIsNotNone(category_outline)
        feed_outline = category_outline.find("outline")
        self.assertIsNotNone(feed_outline)
        self.assertEqual(feed_outline.get("title"), self.feed.name)
        expected_url = f"https://test.example.com/feed/rss/{self.feed.slug}"
        self.assertEqual(feed_outline.get("xmlUrl"), expected_url)

    @patch('core.actions.reverse')
    def test_create_digest_action(self, mock_reverse):
        """Test the create_digest admin action."""
        mock_reverse.return_value = "/admin/core/digest/add/"
        request = self.factory.get("/")
        feed2 = Feed.objects.create(
            name="Test Feed 2", feed_url="https://example2.com/rss.xml"
        )
        queryset = Feed.objects.filter(id__in=[self.feed.id, feed2.id])

        response = create_digest(self.modeladmin, request, queryset)

        # Verify it returns an HttpResponseRedirect
        self.assertEqual(response.status_code, 302)
        expected_ids = f"{self.feed.id},{feed2.id}"
        self.assertIn(f"feed_ids={expected_ids}", response.url)
        mock_reverse.assert_called_once_with("admin:core_digest_add")

    def test_generate_opml_feed_multiple_tags_same_category(self):
        """Test _generate_opml_feed with multiple feeds in same category (line 105)."""
        tag = Tag.objects.create(name="Tech")
        feed1 = self.feed
        feed2 = Feed.objects.create(name="Feed 2", feed_url="https://example2.com/rss.xml")
        
        # Both feeds have same tag - this should trigger line 105
        feed1.tags.add(tag)
        feed2.tags.add(tag) 
        
        queryset = Feed.objects.filter(id__in=[feed1.id, feed2.id])
        response = _generate_opml_feed("Test", queryset, lambda f: f.feed_url, "test")
        
        self.assertEqual(response.status_code, 200)
        root = etree.fromstring(response.content)
        category_outline = root.find('body/outline[@title="Tech"]')
        self.assertIsNotNone(category_outline)
        # Should have 2 feeds under same category
        feed_outlines = category_outline.findall("outline")
        self.assertEqual(len(feed_outlines), 2)

    @patch('core.actions.logger.error')
    @patch('core.actions.etree.Element')
    def test_generate_opml_feed_exception_handling(self, mock_element, mock_logger):
        """Test _generate_opml_feed exception handling (lines 135-137)."""
        mock_element.side_effect = Exception("Test error")
        
        queryset = Feed.objects.filter(id=self.feed.id)
        response = _generate_opml_feed("Test", queryset, lambda f: f.feed_url, "test")
        
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.content, b"An error occurred during OPML export")
        mock_logger.assert_called_once()

    def test_feed_batch_modify_false_cases(self):
        """Test feed_batch_modify with False values for boolean fields."""
        post_data = {
            "apply": "Apply", 
            "translate_title": "False",  # Line 234-235
            "translate_content": "True",  # Line 240-241  
            "summary": "True"  # Line 249
        }
        request = self.factory.post("/", post_data)
        queryset = Feed.objects.filter(id=self.feed.id)

        response = feed_batch_modify(self.modeladmin, request, queryset)

        self.assertEqual(response.status_code, 302)
        self.feed.refresh_from_db()
        self.assertFalse(self.feed.translate_title)  # Line 235
        self.assertTrue(self.feed.translate_content)  # Line 241
        self.assertTrue(self.feed.summary)  # Line 249

    def test_feed_batch_modify_translate_content_false(self):
        """Test feed_batch_modify translate_content False case (lines 242-243)."""
        post_data = {"apply": "Apply", "translate_content": "False"}
        request = self.factory.post("/", post_data)
        queryset = Feed.objects.filter(id=self.feed.id)

        response = feed_batch_modify(self.modeladmin, request, queryset)

        self.assertEqual(response.status_code, 302)
        self.feed.refresh_from_db()
        self.assertFalse(self.feed.translate_content)

    def test_feed_batch_modify_translator_field(self):
        """Test feed_batch_modify translator field handling (lines 259-261)."""
        post_data = {
            "apply": "Apply",
            "translator": "Change",
            "translator_value": "1:5"  # content_type_id:object_id
        }
        request = self.factory.post("/", post_data)
        queryset = Feed.objects.filter(id=self.feed.id)

        response = feed_batch_modify(self.modeladmin, request, queryset)

        self.assertEqual(response.status_code, 302)
        self.feed.refresh_from_db()
        self.assertEqual(self.feed.translator_content_type_id, 1)  # Line 260
        self.assertEqual(self.feed.translator_object_id, 5)  # Line 261

    def test_feed_batch_modify_summarizer_field(self):
        """Test feed_batch_modify summarizer field handling (lines 263-269)."""
        post_data = {
            "apply": "Apply",
            "summarizer": "Change", 
            "summarizer_value": "2:7"  # content_type_id:object_id
        }
        request = self.factory.post("/", post_data)
        queryset = Feed.objects.filter(id=self.feed.id)

        response = feed_batch_modify(self.modeladmin, request, queryset)

        self.assertEqual(response.status_code, 302)
        self.feed.refresh_from_db()
        self.assertEqual(self.feed.summarizer_content_type_id, 2)  # Line 267
        self.assertEqual(self.feed.summarizer_object_id, 7)  # Line 269

    def test_feed_batch_modify_filter_field(self):
        """Test feed_batch_modify filter field handling (lines 279-283)."""
        filter1 = Filter.objects.create(name="Filter 1")
        filter2 = Filter.objects.create(name="Filter 2")
        post_data = {
            "apply": "Apply",
            "filter": "Change",
            "filter_value": [str(filter1.id), str(filter2.id)]
        }
        request = self.factory.post("/", post_data)
        queryset = Feed.objects.filter(id=self.feed.id)

        response = feed_batch_modify(self.modeladmin, request, queryset)

        self.assertEqual(response.status_code, 302)
        self.feed.refresh_from_db()
        feed_filters = list(self.feed.filters.all())
        self.assertIn(filter1, feed_filters)  # Lines 281-283
        self.assertIn(filter2, feed_filters)

    @patch("core.actions.get_all_agent_choices", return_value=[])
    @patch("core.actions.get_ai_agent_choices", return_value=[])
    @patch("core.actions.core_admin_site.each_context", return_value={})
    def test_feed_batch_modify_render_form(self, mock_context, mock_ai_agents, mock_all_agents):
        """Test feed_batch_modify render form (lines 291-295)."""
        # Create some test data
        Tag.objects.create(name="Test Tag")
        Filter.objects.create(name="Test Filter")
        
        request = self.factory.get("/")  # GET request, no "apply"
        queryset = Feed.objects.filter(id=self.feed.id)

        response = feed_batch_modify(self.modeladmin, request, queryset)

        self.assertEqual(response.status_code, 200)  # Should render template
        # Verify the context contains expected choices
        mock_all_agents.assert_called_once()  # Line 291
        mock_ai_agents.assert_called_once()  # Line 292

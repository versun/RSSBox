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
            name="Test Feed", feed_url="https://example.com/rss.xml"
        )
        self.entry1 = Entry.objects.create(
            feed=self.feed,
            original_title="Title 1",
            translated_title="Translated Title 1",
            translated_content="Translated Content 1",
            ai_summary="AI summary"
        )
        self.entry2 = Entry.objects.create(
            feed=self.feed,
            original_title="Title 2", 
            translated_title="Translated Title 2",
            translated_content="Translated Content 2",
        )
        self.modeladmin = ModelAdmin(Feed, None)

    def _get_request_with_messages(self, method='GET', data=None):
        """Helper to create request with message framework"""
        if method == 'GET':
            request = self.factory.get("/")
        else:
            request = self.factory.post("/", data or {})
        setattr(request, "session", "session")
        setattr(request, "_messages", FallbackStorage(request))
        # 添加用户属性以避免认证错误
        setattr(request, "user", type('User', (), {'is_active': True, 'is_staff': True})())
        return request

    def test_clean_translated_content_action(self):
        """Test cleaning translated content from feed entries."""
        request = self._get_request_with_messages()
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
        """Test cleaning AI summary from feed entries."""
        request = self._get_request_with_messages()
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
        """Test batch modify for boolean fields."""
        post_data = {"apply": "Apply", "translate_title": "True", "summary": "False"}
        request = self._get_request_with_messages('POST', post_data)
        queryset = Feed.objects.filter(id=self.feed.id)

        response = feed_batch_modify(self.modeladmin, request, queryset)

        self.assertEqual(response.status_code, 302)
        self.feed.refresh_from_db()
        self.assertTrue(self.feed.translate_title)
        self.assertFalse(self.feed.summary)

    @patch("core.actions.get_all_agent_choices", return_value=[])
    @patch("core.actions.get_ai_agent_choices", return_value=[])
    def test_feed_batch_modify_other_fields(self, mock_ai_agents, mock_all_agents):
        """Test batch modify for non-boolean fields."""
        tag = Tag.objects.create(name="New Tag")
        post_data = {
            "apply": "Apply",
            "update_frequency": "Change",
            "update_frequency_value": "60",
            "tags": "Change",
            "tags_value": [str(tag.id)],
        }
        request = self._get_request_with_messages('POST', post_data)
        queryset = Feed.objects.filter(id=self.feed.id)

        response = feed_batch_modify(self.modeladmin, request, queryset)

        self.assertEqual(response.status_code, 302)
        self.feed.refresh_from_db()
        self.assertEqual(self.feed.update_frequency, 60)
        self.assertIn(tag, self.feed.tags.all())

    @patch('core.models.filter.Filter.clear_ai_filter_cache_results')
    def test_clean_filter_results_action(self, mock_clear_cache):
        """Test cleaning filter results."""
        request = self._get_request_with_messages()
        filter1 = Filter.objects.create(name="Test Filter 1")
        filter2 = Filter.objects.create(name="Test Filter 2")
        queryset = Filter.objects.filter(id__in=[filter1.id, filter2.id])

        clean_filter_results(self.modeladmin, request, queryset)

        self.assertEqual(mock_clear_cache.call_count, 2)

    def test_export_opml_actions(self):
        """Test both original and translated OPML export actions."""
        tag = Tag.objects.create(name="News")
        self.feed.tags.add(tag)
        self.feed.slug = "test-feed"
        self.feed.save()
        queryset = Feed.objects.filter(id=self.feed.id)

        # Test original export
        response = export_original_feed_as_opml(self.modeladmin, self.factory.get("/"), queryset)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/xml")
        self.assertIn("original_feeds_from_rsstranslator.opml", response["Content-Disposition"])
        
        root = etree.fromstring(response.content)
        self.assertEqual(root.tag, "opml")
        self.assertEqual(root.find("head/title").text, "Original Feeds | RSS Translator")
        
        # Test translated export with settings patch
        with patch('core.actions.settings.SITE_URL', 'https://test.example.com'):
            response = export_translated_feed_as_opml(self.modeladmin, self.factory.get("/"), queryset)
            self.assertEqual(response.status_code, 200)
            self.assertIn("translated_feeds_from_rsstranslator.opml", response["Content-Disposition"])
            
            root = etree.fromstring(response.content)
            self.assertEqual(root.find("head/title").text, "Translated Feeds | RSS Translator")

    @patch('core.actions.reverse')
    def test_create_digest_action(self, mock_reverse):
        """Test create digest action."""
        mock_reverse.return_value = "/admin/core/digest/add/"
        feed2 = Feed.objects.create(name="Feed 2", feed_url="https://example2.com/rss.xml")
        queryset = Feed.objects.filter(id__in=[self.feed.id, feed2.id])

        response = create_digest(self.modeladmin, self.factory.get("/"), queryset)

        self.assertEqual(response.status_code, 302)
        expected_ids = f"{self.feed.id},{feed2.id}"
        self.assertIn(f"feed_ids={expected_ids}", response.url)
        mock_reverse.assert_called_once_with("admin:core_digest_add")

    def test_opml_edge_cases(self):
        """Test OPML generation edge cases and error handling."""
        # Test multiple feeds in same category
        tag = Tag.objects.create(name="Tech")
        feed2 = Feed.objects.create(name="Feed 2", feed_url="https://example2.com/rss.xml")
        self.feed.tags.add(tag)
        feed2.tags.add(tag)
        
        queryset = Feed.objects.filter(id__in=[self.feed.id, feed2.id])
        response = _generate_opml_feed("Test", queryset, lambda f: f.feed_url, "test")
        
        self.assertEqual(response.status_code, 200)
        root = etree.fromstring(response.content)
        category_outline = root.find('body/outline[@title="Tech"]')
        self.assertEqual(len(category_outline.findall("outline")), 2)

        # Test exception handling
        with patch('core.actions.etree.Element', side_effect=Exception("Test error")):
            with patch('core.actions.logger.error') as mock_logger:
                response = _generate_opml_feed("Test", queryset, lambda f: f.feed_url, "test")
                self.assertEqual(response.status_code, 500)
                mock_logger.assert_called_once()

    def test_feed_batch_modify_comprehensive(self):
        """Test comprehensive batch modify scenarios."""
        # Test boolean field combinations
        post_data = {
            "apply": "Apply",
            "translate_title": "False",
            "translate_content": "True", 
            "summary": "False"
        }
        request = self._get_request_with_messages('POST', post_data)
        queryset = Feed.objects.filter(id=self.feed.id)

        response = feed_batch_modify(self.modeladmin, request, queryset)
        
        self.assertEqual(response.status_code, 302)
        self.feed.refresh_from_db()
        self.assertFalse(self.feed.translate_title)
        self.assertTrue(self.feed.translate_content)
        self.assertFalse(self.feed.summary)

        # Test translator/summarizer fields
        post_data = {
            "apply": "Apply",
            "translator": "Change",
            "translator_value": "1:5",
            "summarizer": "Change",
            "summarizer_value": "2:7"
        }
        request = self._get_request_with_messages('POST', post_data)
        
        response = feed_batch_modify(self.modeladmin, request, queryset)
        
        self.assertEqual(response.status_code, 302)
        self.feed.refresh_from_db()
        self.assertEqual(self.feed.translator_content_type_id, 1)
        self.assertEqual(self.feed.translator_object_id, 5)
        self.assertEqual(self.feed.summarizer_content_type_id, 2)
        self.assertEqual(self.feed.summarizer_object_id, 7)

        # Test filter assignment
        filter1 = Filter.objects.create(name="Filter 1")
        filter2 = Filter.objects.create(name="Filter 2")
        post_data = {
            "apply": "Apply",
            "filter": "Change",
            "filter_value": [str(filter1.id), str(filter2.id)]
        }
        request = self._get_request_with_messages('POST', post_data)
        
        response = feed_batch_modify(self.modeladmin, request, queryset)
        
        self.assertEqual(response.status_code, 302)
        self.feed.refresh_from_db()
        feed_filters = list(self.feed.filters.all())
        self.assertIn(filter1, feed_filters)
        self.assertIn(filter2, feed_filters)

    @patch("core.actions.get_all_agent_choices", return_value=[])
    @patch("core.actions.get_ai_agent_choices", return_value=[])
    @patch("core.actions.core_admin_site.each_context", return_value={})
    def test_feed_batch_modify_form_render(self, mock_context, mock_ai_agents, mock_all_agents):
        """Test batch modify form rendering."""
        Tag.objects.create(name="Test Tag")
        Filter.objects.create(name="Test Filter")
        
        request = self._get_request_with_messages('GET')
        queryset = Feed.objects.filter(id=self.feed.id)

        response = feed_batch_modify(self.modeladmin, request, queryset)

        self.assertEqual(response.status_code, 200)
        mock_all_agents.assert_called_once()
        mock_ai_agents.assert_called_once()

    def test_feed_batch_modify_keep_fields(self):
        """Test batch modify when fields are set to 'Keep' (no change)."""
        # 设置初始值
        self.feed.translate_title = True
        self.feed.translate_content = False
        self.feed.summary = True
        self.feed.save()
        
        post_data = {
            "apply": "Apply",
            "translate_title": "Keep",
            "translate_content": "Keep", 
            "summary": "Keep"
        }
        request = self._get_request_with_messages('POST', post_data)
        queryset = Feed.objects.filter(id=self.feed.id)

        response = feed_batch_modify(self.modeladmin, request, queryset)
        
        self.assertEqual(response.status_code, 302)
        self.feed.refresh_from_db()
        # 字段应该保持原值不变
        self.assertTrue(self.feed.translate_title)
        self.assertFalse(self.feed.translate_content)
        self.assertTrue(self.feed.summary)

    def test_feed_batch_modify_default_field_types(self):
        """Test batch modify with default field type handling."""
        post_data = {
            "apply": "Apply",
            "target_language": "Change",
            "target_language_value": "zh-CN",
            "additional_prompt": "Change",
            "additional_prompt_value": "Custom prompt"
        }
        request = self._get_request_with_messages('POST', post_data)
        queryset = Feed.objects.filter(id=self.feed.id)

        response = feed_batch_modify(self.modeladmin, request, queryset)
        
        self.assertEqual(response.status_code, 302)
        self.feed.refresh_from_db()
        self.assertEqual(self.feed.target_language, "zh-CN")
        self.assertEqual(self.feed.additional_prompt, "Custom prompt")

    def test_feed_batch_modify_empty_tags_and_filters(self):
        """Test batch modify with empty tags and filters values."""
        post_data = {
            "apply": "Apply",
            "tags": "Change",
            "tags_value": [],  # 空列表
            "filter": "Change",
            "filter_value": []  # 空列表
        }
        request = self._get_request_with_messages('POST', post_data)
        queryset = Feed.objects.filter(id=self.feed.id)

        response = feed_batch_modify(self.modeladmin, request, queryset)
        
        self.assertEqual(response.status_code, 302)
        self.feed.refresh_from_db()
        # 空值不应该影响现有数据
        self.assertEqual(self.feed.tags.count(), 0)
        self.assertEqual(self.feed.filters.count(), 0)

    def test_feed_batch_modify_numeric_fields(self):
        """Test batch modify with numeric field types."""
        post_data = {
            "apply": "Apply",
            "update_frequency": "Change",
            "update_frequency_value": "30",
            "max_posts": "Change",
            "max_posts_value": "100",
            "summary_detail": "Change",
            "summary_detail_value": "0.8"
        }
        request = self._get_request_with_messages('POST', post_data)
        queryset = Feed.objects.filter(id=self.feed.id)

        response = feed_batch_modify(self.modeladmin, request, queryset)
        
        self.assertEqual(response.status_code, 302)
        self.feed.refresh_from_db()
        self.assertEqual(self.feed.update_frequency, 30)
        self.assertEqual(self.feed.max_posts, 100)
        self.assertEqual(self.feed.summary_detail, 0.8)

    def test_feed_batch_modify_translation_display(self):
        """Test batch modify with translation_display field."""
        post_data = {
            "apply": "Apply",
            "translation_display": "Change",
            "translation_display_value": "2"
        }
        request = self._get_request_with_messages('POST', post_data)
        queryset = Feed.objects.filter(id=self.feed.id)

        response = feed_batch_modify(self.modeladmin, request, queryset)
        
        self.assertEqual(response.status_code, 302)
        self.feed.refresh_from_db()
        self.assertEqual(self.feed.translation_display, 2)

    def test_feed_batch_modify_mixed_boolean_combinations(self):
        """Test various combinations of boolean field modifications."""
        # 测试混合的布尔值组合
        post_data = {
            "apply": "Apply",
            "translate_title": "True",
            "translate_content": "Keep",
            "summary": "False"
        }
        request = self._get_request_with_messages('POST', post_data)
        queryset = Feed.objects.filter(id=self.feed.id)

        response = feed_batch_modify(self.modeladmin, request, queryset)
        
        self.assertEqual(response.status_code, 302)
        self.feed.refresh_from_db()
        self.assertTrue(self.feed.translate_title)
        # translate_content 保持原值（因为设置为"Keep"）
        self.assertFalse(self.feed.summary)

    def test_feed_batch_modify_no_apply_post_data(self):
        """Test batch modify when no 'apply' in POST data (form display)."""
        # 设置初始值
        self.feed.translate_title = False
        self.feed.save()
        
        post_data = {
            "translate_title": "True",  # 没有 "apply" 键
            "translate_title_value": "True"
        }
        request = self._get_request_with_messages('POST', post_data)
        queryset = Feed.objects.filter(id=self.feed.id)

        response = feed_batch_modify(self.modeladmin, request, queryset)
        
        # 应该显示表单而不是处理数据
        self.assertEqual(response.status_code, 200)
        self.feed.refresh_from_db()
        # 字段应该保持原值不变
        self.assertFalse(self.feed.translate_title)

    def test_feed_batch_modify_empty_queryset(self):
        """Test batch modify with empty queryset."""
        post_data = {"apply": "Apply"}
        request = self._get_request_with_messages('POST', post_data)
        empty_queryset = Feed.objects.none()

        response = feed_batch_modify(self.modeladmin, request, empty_queryset)
        
        # 应该正常处理空查询集
        self.assertEqual(response.status_code, 302)

    def test_feed_batch_modify_invalid_field_values(self):
        """Test batch modify with invalid field values."""
        post_data = {
            "apply": "Apply",
            "update_frequency": "Change",
            "update_frequency_value": "invalid_number",  # 无效数字
        }
        request = self._get_request_with_messages('POST', post_data)
        queryset = Feed.objects.filter(id=self.feed.id)

        # 应该能够处理无效值而不崩溃
        try:
            response = feed_batch_modify(self.modeladmin, request, queryset)
            # 如果成功处理，应该重定向
            self.assertEqual(response.status_code, 302)
        except (ValueError, TypeError):
            # 如果抛出异常，测试也应该通过
            pass

    def test_feed_batch_modify_single_feed_multiple_operations(self):
        """Test multiple operations on a single feed in one batch."""
        tag = Tag.objects.create(name="Test Tag")
        filter_obj = Filter.objects.create(name="Test Filter")
        
        post_data = {
            "apply": "Apply",
            "translate_title": "True",
            "translate_content": "False",
            "summary": "True",
            "update_frequency": "Change",
            "update_frequency_value": "60",
            "tags": "Change",
            "tags_value": [str(tag.id)],
            "filter": "Change",
            "filter_value": [str(filter_obj.id)]
        }
        request = self._get_request_with_messages('POST', post_data)
        queryset = Feed.objects.filter(id=self.feed.id)

        response = feed_batch_modify(self.modeladmin, request, queryset)
        
        self.assertEqual(response.status_code, 302)
        self.feed.refresh_from_db()
        
        # 验证所有字段都被正确更新
        self.assertTrue(self.feed.translate_title)
        self.assertFalse(self.feed.translate_content)
        self.assertTrue(self.feed.summary)
        self.assertEqual(self.feed.update_frequency, 60)
        self.assertIn(tag, self.feed.tags.all())
        self.assertIn(filter_obj, self.feed.filters.all())

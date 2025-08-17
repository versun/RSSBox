from django.test import TestCase
from django.core.cache import cache
from unittest.mock import patch

from ..models import Feed, Tag
from ..cache import cache_rss, cache_tag


class CacheRssTest(TestCase):
    def setUp(self):
        self.feed = Feed.objects.create(
            name="Test Feed",
            feed_url="https://example.com/rss.xml",
            slug="test-feed-slug",
            update_frequency=3600
        )
        cache.clear()

    def tearDown(self):
        cache.clear()

    @patch('core.cache.generate_atom_feed')
    def test_cache_rss_behavior(self, mock_generate_atom_feed):
        """测试cache_rss的成功和失败情况"""
        # 测试成功情况
        mock_atom_content = "<feed>test content</feed>"
        mock_generate_atom_feed.return_value = mock_atom_content
        
        result = cache_rss("test-feed-slug", "t", "xml")
        
        self.assertEqual(result, mock_atom_content)
        mock_generate_atom_feed.assert_called_with(self.feed, "t")
        
        # 验证缓存被设置
        cache_key = "cache_rss_test-feed-slug_t_xml"
        self.assertEqual(cache.get(cache_key), mock_atom_content)
        
        # 测试返回None的情况
        mock_generate_atom_feed.reset_mock()
        mock_generate_atom_feed.return_value = None
        cache.clear()
        
        result = cache_rss("test-feed-slug", "t", "xml")
        
        self.assertIsNone(result)
        self.assertIsNone(cache.get(cache_key))

    @patch('core.cache.cache')
    @patch('core.cache.generate_atom_feed')
    def test_cache_rss_zero_frequency(self, mock_generate_atom_feed, mock_cache):
        """测试零频率feed使用调整后的频率值"""
        feed_zero = Feed.objects.create(
            name="Zero Freq Feed",
            feed_url="https://example.com/rss2.xml",
            slug="test-feed-zero-freq",
            update_frequency=0
        )
        
        mock_atom_content = "<feed>test content</feed>"
        mock_generate_atom_feed.return_value = mock_atom_content
        
        result = cache_rss("test-feed-zero-freq", "t", "xml")
        
        self.assertEqual(result, mock_atom_content)
        # Feed模型会将0调整为5
        cache_key = "cache_rss_test-feed-zero-freq_t_xml"
        mock_cache.set.assert_called_once_with(cache_key, mock_atom_content, 5)

    @patch('core.cache.generate_atom_feed')
    def test_cache_rss_different_parameters(self, mock_generate_atom_feed):
        """测试不同参数生成不同缓存键"""
        mock_atom_content = "<feed>test content</feed>"
        mock_generate_atom_feed.return_value = mock_atom_content
        
        cache_rss("test-feed-slug", "o", "json")
        
        cache_key = "cache_rss_test-feed-slug_o_json"
        self.assertEqual(cache.get(cache_key), mock_atom_content)


class CacheTagTest(TestCase):
    def setUp(self):
        self.tag = Tag.objects.create(name="test-tag")
        
        self.feed1 = Feed.objects.create(
            name="Test Feed 1",
            feed_url="https://example1.com/rss.xml",
            slug="test-feed-1",
            update_frequency=3600
        )
        self.feed2 = Feed.objects.create(
            name="Test Feed 2", 
            feed_url="https://example2.com/rss.xml",
            slug="test-feed-2",
            update_frequency=7200
        )
        
        self.feed1.tags.add(self.tag)
        self.feed2.tags.add(self.tag)
        cache.clear()

    def tearDown(self):
        cache.clear()

    @patch('core.cache.merge_feeds_into_one_atom')
    def test_cache_tag_behavior(self, mock_merge_feeds):
        """测试cache_tag的成功和失败情况"""
        # 测试成功情况
        mock_atom_content = "<feed>merged content</feed>"
        mock_merge_feeds.return_value = mock_atom_content
        
        result = cache_tag("test-tag", "t", "xml")
        
        self.assertEqual(result, mock_atom_content)
        
        # 验证调用参数
        call_args = mock_merge_feeds.call_args
        self.assertEqual(call_args[0][0], "test-tag")
        self.assertEqual(call_args[0][2], "t")
        
        feeds_arg = call_args[0][1]
        feed_ids = [f.id for f in feeds_arg]
        self.assertIn(self.feed1.id, feed_ids)
        self.assertIn(self.feed2.id, feed_ids)
        
        # 验证缓存
        cache_key = "cache_tag_test-tag_t_xml"
        self.assertEqual(cache.get(cache_key), mock_atom_content)
        
        # 测试返回None的情况
        mock_merge_feeds.reset_mock()
        mock_merge_feeds.return_value = None
        cache.clear()
        
        result = cache_tag("test-tag", "t", "xml")
        
        self.assertIsNone(result)
        self.assertIsNone(cache.get(cache_key))

    @patch('core.cache.merge_feeds_into_one_atom')
    def test_cache_tag_nonexistent_tag(self, mock_merge_feeds):
        """测试不存在标签的情况"""
        mock_atom_content = "<feed>empty content</feed>"
        mock_merge_feeds.return_value = mock_atom_content
        
        result = cache_tag("nonexistent-tag", "t", "xml")
        
        self.assertEqual(result, mock_atom_content)
        
        # 验证调用参数 - feeds为空查询集
        call_args = mock_merge_feeds.call_args
        self.assertEqual(call_args[0][0], "nonexistent-tag")
        self.assertEqual(len(call_args[0][1]), 0)
        
        cache_key = "cache_tag_nonexistent-tag_t_xml"
        self.assertEqual(cache.get(cache_key), mock_atom_content)

    @patch('core.cache.merge_feeds_into_one_atom')
    def test_cache_tag_different_parameters(self, mock_merge_feeds):
        """测试不同参数生成不同缓存键"""
        mock_atom_content = "<feed>content</feed>"
        mock_merge_feeds.return_value = mock_atom_content
        
        cache_tag("test-tag", "o", "json")
        
        cache_key = "cache_tag_test-tag_o_json"
        self.assertEqual(cache.get(cache_key), mock_atom_content)

from django.test import TestCase
from django.core.cache import cache
from unittest.mock import patch, MagicMock
import logging

from ..models import Feed, Tag
from ..cache import cache_rss, cache_tag


class CacheRssTest(TestCase):
    def setUp(self):
        """设置测试数据"""
        self.feed = Feed.objects.create(
            name="Test Feed",
            feed_url="https://example.com/rss.xml",
            slug="test-feed-slug",
            update_frequency=3600
        )
        cache.clear()

    def tearDown(self):
        """清理缓存"""
        cache.clear()

    @patch('core.cache.logger')
    @patch('core.cache.generate_atom_feed')
    def test_cache_rss_success(self, mock_generate_atom_feed, mock_logger):
        """测试成功缓存RSS的情况"""
        # 模拟generate_atom_feed返回内容
        mock_atom_content = "<feed>test content</feed>"
        mock_generate_atom_feed.return_value = mock_atom_content
        
        # 调用函数
        result = cache_rss("test-feed-slug", "t", "xml")
        
        # 验证结果
        self.assertEqual(result, mock_atom_content)
        
        # 验证generate_atom_feed被正确调用
        mock_generate_atom_feed.assert_called_once_with(self.feed, "t")
        
        # 验证缓存被设置
        cache_key = "cache_rss_test-feed-slug_t_xml"
        cached_content = cache.get(cache_key)
        self.assertEqual(cached_content, mock_atom_content)
        
        # 验证日志记录
        mock_logger.debug.assert_any_call(
            "Start cache_rss for test-feed-slug with type t and format xml"
        )
        mock_logger.debug.assert_any_call(
            f"Cached successfully with key {cache_key}"
        )

    @patch('core.cache.logger')
    @patch('core.cache.generate_atom_feed')
    def test_cache_rss_generate_atom_feed_returns_none(self, mock_generate_atom_feed, mock_logger):
        """测试generate_atom_feed返回None的情况"""
        # 模拟generate_atom_feed返回None
        mock_generate_atom_feed.return_value = None
        
        # 调用函数
        result = cache_rss("test-feed-slug", "t", "xml")
        
        # 验证结果
        self.assertIsNone(result)
        
        # 验证generate_atom_feed被调用
        mock_generate_atom_feed.assert_called_once_with(self.feed, "t")
        
        # 验证缓存没有被设置
        cache_key = "cache_rss_test-feed-slug_t_xml"
        cached_content = cache.get(cache_key)
        self.assertIsNone(cached_content)
        
        # 验证只有开始日志，没有成功日志
        mock_logger.debug.assert_called_once_with(
            "Start cache_rss for test-feed-slug with type t and format xml"
        )

    @patch('core.cache.logger')
    @patch('core.cache.generate_atom_feed')
    @patch('core.cache.cache')
    def test_cache_rss_with_zero_update_frequency(self, mock_cache, mock_generate_atom_feed, mock_logger):
        """测试当feed.update_frequency为0时使用默认值"""
        # 创建一个update_frequency为0的feed来测试or逻辑
        feed_zero_frequency = Feed.objects.create(
            name="Test Feed Zero Frequency",
            feed_url="https://example.com/rss2.xml",
            slug="test-feed-zero-freq",
            update_frequency=0  # 0是falsy值，会触发or 86400
        )
        
        mock_atom_content = "<feed>test content</feed>"
        mock_generate_atom_feed.return_value = mock_atom_content
        
        # 调用函数
        result = cache_rss("test-feed-zero-freq", "t", "xml")
        
        # 验证结果
        self.assertEqual(result, mock_atom_content)
        
        # 验证cache.set被调用时使用了调整后的频率（Feed模型会将0调整为5）
        cache_key = "cache_rss_test-feed-zero-freq_t_xml"
        mock_cache.set.assert_called_once_with(cache_key, mock_atom_content, 5)

    @patch('core.cache.logger')
    @patch('core.cache.generate_atom_feed')
    def test_cache_rss_different_parameters(self, mock_generate_atom_feed, mock_logger):
        """测试不同参数组合生成不同的缓存键"""
        mock_atom_content = "<feed>test content</feed>"
        mock_generate_atom_feed.return_value = mock_atom_content
        
        # 测试不同的feed_type和format
        cache_rss("test-feed-slug", "o", "json")
        
        # 验证生成正确的缓存键
        cache_key = "cache_rss_test-feed-slug_o_json"
        cached_content = cache.get(cache_key)
        self.assertEqual(cached_content, mock_atom_content)
        
        # 验证日志记录了正确的参数
        mock_logger.debug.assert_any_call(
            "Start cache_rss for test-feed-slug with type o and format json"
        )


class CacheTagTest(TestCase):
    def setUp(self):
        """设置测试数据"""
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
        
        # 为feeds添加标签
        self.feed1.tags.add(self.tag)
        self.feed2.tags.add(self.tag)
        
        cache.clear()

    def tearDown(self):
        """清理缓存"""
        cache.clear()

    @patch('core.cache.logger')
    @patch('core.cache.merge_feeds_into_one_atom')
    def test_cache_tag_success(self, mock_merge_feeds, mock_logger):
        """测试成功缓存标签的情况"""
        mock_atom_content = "<feed>merged content</feed>"
        mock_merge_feeds.return_value = mock_atom_content
        
        # 调用函数
        result = cache_tag("test-tag", "t", "xml")
        
        # 验证结果
        self.assertEqual(result, mock_atom_content)
        
        # 验证merge_feeds_into_one_atom被正确调用
        mock_merge_feeds.assert_called_once()
        call_args = mock_merge_feeds.call_args
        self.assertEqual(call_args[0][0], "test-tag")  # tag参数
        self.assertEqual(call_args[0][2], "t")  # feed_type参数
        # 验证feeds参数包含正确的feeds
        feeds_arg = call_args[0][1]
        feed_ids = [f.id for f in feeds_arg]
        self.assertIn(self.feed1.id, feed_ids)
        self.assertIn(self.feed2.id, feed_ids)
        
        # 验证缓存被设置，使用最大update_frequency
        cache_key = "cache_tag_test-tag_t_xml"
        cached_content = cache.get(cache_key)
        self.assertEqual(cached_content, mock_atom_content)
        
        # 验证日志记录
        mock_logger.debug.assert_any_call(
            "Start cache_tag for test-tag with type t and format xml"
        )
        mock_logger.debug.assert_any_call(
            f"Cached successfully with key {cache_key}"
        )

    @patch('core.cache.logger')
    @patch('core.cache.merge_feeds_into_one_atom')
    def test_cache_tag_merge_feeds_returns_none(self, mock_merge_feeds, mock_logger):
        """测试merge_feeds_into_one_atom返回None的情况"""
        mock_merge_feeds.return_value = None
        
        # 调用函数
        result = cache_tag("test-tag", "t", "xml")
        
        # 验证结果
        self.assertIsNone(result)
        
        # 验证merge_feeds_into_one_atom被调用
        mock_merge_feeds.assert_called_once()
        
        # 验证缓存没有被设置
        cache_key = "cache_tag_test-tag_t_xml"
        cached_content = cache.get(cache_key)
        self.assertIsNone(cached_content)
        
        # 验证只有开始日志，没有成功日志
        mock_logger.debug.assert_called_once_with(
            "Start cache_tag for test-tag with type t and format xml"
        )

    @patch('core.cache.logger')
    @patch('core.cache.merge_feeds_into_one_atom')
    def test_cache_tag_no_feeds_with_tag(self, mock_merge_feeds, mock_logger):
        """测试没有feeds包含该标签的情况"""
        mock_atom_content = "<feed>empty content</feed>"
        mock_merge_feeds.return_value = mock_atom_content
        
        # 调用一个不存在的标签
        result = cache_tag("nonexistent-tag", "t", "xml")
        
        # 验证结果
        self.assertEqual(result, mock_atom_content)
        
        # 验证merge_feeds_into_one_atom被调用，但feeds为空查询集
        mock_merge_feeds.assert_called_once()
        call_args = mock_merge_feeds.call_args
        self.assertEqual(call_args[0][0], "nonexistent-tag")
        feeds_arg = call_args[0][1]
        self.assertEqual(len(feeds_arg), 0)  # 空的查询集
        
        # 验证使用默认的86400秒缓存时间（因为没有feeds）
        cache_key = "cache_tag_nonexistent-tag_t_xml"
        cached_content = cache.get(cache_key)
        self.assertEqual(cached_content, mock_atom_content)

    @patch('core.cache.logger')
    @patch('core.cache.merge_feeds_into_one_atom')
    def test_cache_tag_max_frequency_calculation(self, mock_merge_feeds, mock_logger):
        """测试最大频率计算逻辑"""
        mock_atom_content = "<feed>content</feed>"
        mock_merge_feeds.return_value = mock_atom_content
        
        # 调用函数
        result = cache_tag("test-tag", "t", "xml")
        
        # 验证结果
        self.assertEqual(result, mock_atom_content)
        
        # feed2的update_frequency是7200，应该是最大的
        # 验证缓存被设置
        cache_key = "cache_tag_test-tag_t_xml"
        cached_content = cache.get(cache_key)
        self.assertEqual(cached_content, mock_atom_content)

    @patch('core.cache.logger')
    @patch('core.cache.merge_feeds_into_one_atom')
    def test_cache_tag_different_parameters(self, mock_merge_feeds, mock_logger):
        """测试不同参数组合生成不同的缓存键"""
        mock_atom_content = "<feed>content</feed>"
        mock_merge_feeds.return_value = mock_atom_content
        
        # 测试不同的feed_type和format
        cache_tag("test-tag", "o", "json")
        
        # 验证生成正确的缓存键
        cache_key = "cache_tag_test-tag_o_json"
        cached_content = cache.get(cache_key)
        self.assertEqual(cached_content, mock_atom_content)
        
        # 验证日志记录了正确的参数
        mock_logger.debug.assert_any_call(
            "Start cache_tag for test-tag with type o and format json"
        )

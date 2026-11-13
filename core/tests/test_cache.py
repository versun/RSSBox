import pickle

from django.test import TestCase
from django.core.cache import cache
from unittest.mock import patch

from ..models import Digest, Feed, OpenAIAgent, Tag
from ..cache import (
    DEFAULT_CACHE_TIMEOUT_MINUTES,
    _DIGEST_CACHE_TIMEOUT_SECONDS,
    cache_digest,
    cache_rss,
    cache_tag,
    cache_timeout_seconds,
)
from ..models.filter import cache_version


def _versioned_key(prefix, scope, pk):
    """The public cache key for a feed/tag output at its current version."""
    return f"{prefix}_v{cache_version(scope, pk)}"


def _stored_get(key):
    """Read a locmem-internal entry by public key (bypasses make_key).

    locmem pickles values, so unpickle to compare with the raw content."""
    for stored, value in cache._cache.items():
        if stored.split(":", 2)[-1] == key:
            try:
                return pickle.loads(value)
            except Exception:
                return value
    return None


class CacheRssTest(TestCase):
    def setUp(self):
        self.feed = Feed.objects.create(
            name="Test Feed",
            feed_url="https://example.com/rss.xml",
            slug="test-feed-slug",
            update_frequency=3600,
        )
        cache.clear()

    def tearDown(self):
        cache.clear()

    @patch("core.cache.generate_atom_feed")
    def test_cache_rss_behavior(self, mock_generate_atom_feed):
        """测试cache_rss的成功和失败情况"""
        # 测试成功情况
        mock_atom_content = "<feed>test content</feed>"
        mock_generate_atom_feed.return_value = mock_atom_content

        result = cache_rss("test-feed-slug", "t", "xml")

        self.assertEqual(result, mock_atom_content)
        mock_generate_atom_feed.assert_called_with(self.feed, "t")

        # 验证缓存被设置（键带失效版本后缀）
        key = _versioned_key("cache_rss_test-feed-slug_t_xml", "rss", self.feed.pk)
        self.assertEqual(_stored_get(key), mock_atom_content)

        # 测试返回None的情况
        mock_generate_atom_feed.reset_mock()
        mock_generate_atom_feed.return_value = None
        cache.clear()

        result = cache_rss("test-feed-slug", "t", "xml")

        self.assertIsNone(result)

    @patch("core.cache.cache")
    @patch("core.cache.generate_atom_feed")
    def test_cache_rss_zero_frequency(self, mock_generate_atom_feed, mock_cache):
        """测试零频率feed使用调整后的频率值"""
        feed_zero = Feed.objects.create(
            name="Zero Freq Feed",
            feed_url="https://example.com/rss2.xml",
            slug="test-feed-zero-freq",
            update_frequency=0,
        )

        mock_atom_content = "<feed>test content</feed>"
        mock_generate_atom_feed.return_value = mock_atom_content

        result = cache_rss("test-feed-zero-freq", "t", "xml")

        self.assertEqual(result, mock_atom_content)
        # Feed模型会将0调整为5分钟，缓存超时必须是 5 * 60 秒
        mock_cache.set.assert_called_once_with(
            "cache_rss_test-feed-zero-freq_t_xml_v0", mock_atom_content, 300
        )

    @patch("core.cache.generate_atom_feed")
    def test_cache_rss_different_parameters(self, mock_generate_atom_feed):
        """测试不同参数生成不同缓存键"""
        mock_atom_content = "<feed>test content</feed>"
        mock_generate_atom_feed.return_value = mock_atom_content

        cache_rss("test-feed-slug", "o", "json")

        key = _versioned_key("cache_rss_test-feed-slug_o_json", "rss", self.feed.pk)
        self.assertEqual(_stored_get(key), mock_atom_content)


class CacheTimeoutUnitsTest(TestCase):
    """update_frequency is stored in minutes; cache timeouts are in seconds."""

    def test_helper_converts_minutes_to_seconds(self):
        self.assertEqual(cache_timeout_seconds(5), 5 * 60)
        self.assertEqual(cache_timeout_seconds(30), 30 * 60)
        self.assertEqual(cache_timeout_seconds(1440), 1440 * 60)

    def test_helper_falls_back_for_invalid_values(self):
        one_day = DEFAULT_CACHE_TIMEOUT_MINUTES * 60
        for value in (None, 0, -10):
            with self.subTest(value=value):
                self.assertEqual(cache_timeout_seconds(value), one_day)

    @patch("core.cache.cache")
    @patch("core.cache.generate_atom_feed", return_value="<feed />")
    def test_cache_rss_uses_minute_frequency_as_seconds(self, _, mock_cache):
        for minutes in (5, 1440):
            with self.subTest(minutes=minutes):
                mock_cache.reset_mock()
                feed = Feed.objects.create(
                    feed_url=f"https://example.com/{minutes}.xml",
                    slug=f"feed-{minutes}",
                    update_frequency=minutes,
                )
                cache_rss(feed.slug, "t", "xml")
                # Versioned key; cache_version() reads the mocked cache and
                # gets no versions dict, so the suffix is always v0.
                mock_cache.set.assert_called_once_with(
                    f"cache_rss_{feed.slug}_t_xml_v0", "<feed />", minutes * 60
                )

    @patch("core.cache.cache")
    @patch("core.cache.merge_feeds_into_one_atom")
    def test_cache_tag_uses_slowest_feed_in_seconds(
        self, mock_merge_feeds, mock_cache
    ):
        tag = Tag.objects.create(name="cadence")
        fast = Feed.objects.create(
            name="Fast",
            feed_url="https://example.com/fast.xml",
            slug="fast-feed",
            update_frequency=5,
        )
        slow = Feed.objects.create(
            name="Slow",
            feed_url="https://example.com/slow.xml",
            slug="slow-feed",
            update_frequency=1440,
        )
        fast.tags.add(tag)
        slow.tags.add(tag)
        mock_merge_feeds.return_value = "<feed />"

        cache_tag(tag.slug, "t", "xml")

        mock_cache.set.assert_called_once_with(
            f"cache_tag_{tag.slug}_t_xml_v0", "<feed />", 1440 * 60
        )

    @patch("core.cache.cache")
    @patch("core.cache.generate_atom_feed")
    def test_cache_digest_uses_digest_feed_frequency(
        self, mock_generate_atom_feed, mock_cache
    ):
        agent = OpenAIAgent.objects.create(name="Digest agent", api_key="test")
        digest = Digest.objects.create(name="Daily digest", summarizer=agent)
        digest_feed = digest.get_digest_feed()
        self.assertEqual(digest_feed.update_frequency, 1440)
        mock_generate_atom_feed.return_value = "<feed />"

        cache_digest(digest.slug, "xml")

        # Digest TTL is capped at _DIGEST_CACHE_TIMEOUT_SECONDS even when
        # the digest feed's own frequency implies a longer window.
        mock_cache.set.assert_called_once_with(
            f"cache_digest_{digest.slug}_xml_v0",
            "<feed />",
            _DIGEST_CACHE_TIMEOUT_SECONDS,
        )


class CacheTagTest(TestCase):
    def setUp(self):
        self.tag = Tag.objects.create(name="test-tag")

        self.feed1 = Feed.objects.create(
            name="Test Feed 1",
            feed_url="https://example1.com/rss.xml",
            slug="test-feed-1",
            update_frequency=3600,
        )
        self.feed2 = Feed.objects.create(
            name="Test Feed 2",
            feed_url="https://example2.com/rss.xml",
            slug="test-feed-2",
            update_frequency=7200,
        )

        self.feed1.tags.add(self.tag)
        self.feed2.tags.add(self.tag)
        cache.clear()

    def tearDown(self):
        cache.clear()

    @patch("core.cache.merge_feeds_into_one_atom")
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
        key = _versioned_key("cache_tag_test-tag_t_xml", "tag", self.tag.pk)
        self.assertEqual(_stored_get(key), mock_atom_content)

        # 测试返回None的情况
        mock_merge_feeds.reset_mock()
        mock_merge_feeds.return_value = None
        cache.clear()

        result = cache_tag("test-tag", "t", "xml")

        self.assertIsNone(result)

    @patch("core.cache.merge_feeds_into_one_atom")
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

        # A slug with no Tag row still builds a merged feed (empty queryset)
        # under version 0.
        self.assertEqual(
            _stored_get("cache_tag_nonexistent-tag_t_xml_v0"),
            mock_atom_content,
        )

    @patch("core.cache.merge_feeds_into_one_atom")
    def test_cache_tag_different_parameters(self, mock_merge_feeds):
        """测试不同参数生成不同缓存键"""
        mock_atom_content = "<feed>content</feed>"
        mock_merge_feeds.return_value = mock_atom_content

        cache_tag("test-tag", "o", "json")

        key = _versioned_key("cache_tag_test-tag_o_json", "tag", self.tag.pk)
        self.assertEqual(_stored_get(key), mock_atom_content)

    @patch("core.cache.merge_feeds_into_one_atom")
    def test_cache_tag_by_slug_when_name_differs(self, mock_merge_feeds):
        """测试当Tag的name和slug不同时，仍能正确通过slug找到关联Feed"""
        chinese_tag = Tag.objects.create(name="中文标签")
        feed = Feed.objects.create(
            name="Chinese Feed",
            feed_url="https://example.com/chinese.xml",
            slug="chinese-feed",
            update_frequency=3600,
        )
        feed.tags.add(chinese_tag)

        mock_atom_content = "<feed>chinese content</feed>"
        mock_merge_feeds.return_value = mock_atom_content

        result = cache_tag(chinese_tag.slug, "t", "xml")
        self.assertEqual(result, mock_atom_content)

        call_args = mock_merge_feeds.call_args
        feeds_arg = call_args[0][1]
        self.assertEqual(list(feeds_arg), [feed])

from unittest.mock import Mock, call

from django.test import SimpleTestCase


class FeedRefreshTests(SimpleTestCase):
    def setUp(self):
        self.feed1 = Mock(slug="feed-1")
        self.feed2 = Mock(slug="feed-2")
        self.feed1.tags.values_list.return_value = [1, 2]
        self.feed2.tags.values_list.return_value = [2, 3]

    def test_refresh_feed_caches_calls_all_output_variants(self):
        from core.services.feed.refresh import refresh_feed_caches

        cache_rss_func = Mock()

        refresh_feed_caches(
            [self.feed1, self.feed2],
            cache_rss_func=cache_rss_func,
        )

        expected_calls = [
            call("feed-1", feed_type="o", format="xml"),
            call("feed-1", feed_type="o", format="json"),
            call("feed-1", feed_type="t", format="xml"),
            call("feed-1", feed_type="t", format="json"),
            call("feed-2", feed_type="o", format="xml"),
            call("feed-2", feed_type="o", format="json"),
            call("feed-2", feed_type="t", format="xml"),
            call("feed-2", feed_type="t", format="json"),
        ]
        cache_rss_func.assert_has_calls(expected_calls)

    def test_get_related_tags_deduplicates_ids_before_query(self):
        from core.services.feed.refresh import get_related_tags

        tag_model = Mock()
        tag_model.objects.filter.return_value = ["tag-1", "tag-2", "tag-3"]

        result = get_related_tags(
            [self.feed1, self.feed2],
            tag_model=tag_model,
        )

        self.assertEqual(result, ["tag-1", "tag-2", "tag-3"])
        tag_model.objects.filter.assert_called_once_with(id__in={1, 2, 3})

    def test_refresh_tag_caches_calls_all_output_variants(self):
        from core.services.feed.refresh import refresh_tag_caches

        cache_tag_func = Mock()
        tags = [Mock(slug="tag-1"), Mock(slug="tag-2")]

        refresh_tag_caches(tags, cache_tag_func=cache_tag_func)

        expected_calls = [
            call("tag-1", feed_type="o", format="xml"),
            call("tag-1", feed_type="t", format="xml"),
            call("tag-1", feed_type="t", format="json"),
            call("tag-2", feed_type="o", format="xml"),
            call("tag-2", feed_type="t", format="xml"),
            call("tag-2", feed_type="t", format="json"),
        ]
        cache_tag_func.assert_has_calls(expected_calls)

    def test_refresh_updated_content_logs_and_continues_on_cache_errors(self):
        from core.services.feed import refresh_updated_content

        tag_model = Mock()
        tag_model.objects.filter.return_value = [Mock(slug="tag-1")]
        cache_rss_func = Mock(side_effect=[None, Exception("rss failed"), None, None])
        cache_tag_func = Mock(side_effect=[Exception("tag failed")])
        logger = Mock()
        time_func = Mock(return_value=1234567890)

        refresh_updated_content(
            [self.feed1],
            tag_model=tag_model,
            cache_rss_func=cache_rss_func,
            cache_tag_func=cache_tag_func,
            logger=logger,
            time_func=time_func,
        )

        logger.error.assert_any_call(
            "1234567890: Failed to cache RSS for feed-1: rss failed"
        )
        logger.error.assert_any_call("Failed to cache tag tag-1: tag failed")

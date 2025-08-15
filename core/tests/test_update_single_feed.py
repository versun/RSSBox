import logging
from unittest import mock

from django.test import TestCase

from core.management.commands import update_feeds as cmd
from core.models import Feed


class UpdateSingleFeedTests(TestCase):
    """Unit tests for helper `update_single_feed`."""

    def _create_feed(self, **kwargs):
        """Create a minimal `Feed` instance with sensible defaults."""
        default = {
            "feed_url": "https://example.com/rss.xml",
            "name": "Example",
        }
        default.update(kwargs)
        return Feed.objects.create(**default)

    @mock.patch("core.management.commands.update_feeds.close_old_connections")
    @mock.patch("core.management.commands.update_feeds.handle_feeds_summary")
    @mock.patch("core.management.commands.update_feeds.handle_feeds_translation")
    @mock.patch("core.management.commands.update_feeds.handle_single_feed_fetch")
    def test_update_single_feed_success(
        self,
        mock_fetch,
        mock_translate,
        mock_summary,
        mock_close_conn,
    ):
        """When no internal task raises, should return True and call helpers."""
        feed = self._create_feed(translate_title=True, summary=True)

        result = cmd.update_single_feed(feed)

        self.assertTrue(result)
        mock_fetch.assert_called_once_with(feed)
        # translate should be called for title only (translate_content False)
        mock_translate.assert_called_once_with([feed], target_field="title")
        mock_summary.assert_called_once_with([feed])
        # close_old_connections should be called twice (entering & finally)
        self.assertGreaterEqual(mock_close_conn.call_count, 2)

    @mock.patch("core.management.commands.update_feeds.logger")
    @mock.patch("core.management.commands.update_feeds.close_old_connections")
    @mock.patch("core.management.commands.update_feeds.handle_single_feed_fetch")
    def test_update_single_feed_exception(
        self, mock_fetch, mock_close_conn, mock_logger
    ):
        """If internal helper raises, function should swallow and return False."""
        feed = self._create_feed()
        mock_fetch.side_effect = RuntimeError("boom")

        result = cmd.update_single_feed(feed)

        self.assertFalse(result)
        mock_fetch.assert_called_once_with(feed)
        # 验证logger.exception被调用
        mock_logger.exception.assert_called_once()
        # ensure finally block executed
        self.assertGreaterEqual(mock_close_conn.call_count, 2)

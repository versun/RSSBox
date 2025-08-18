import logging
from unittest.mock import patch, Mock

from django.test import TestCase

from core.management.commands import update_feeds as cmd
from core.models import Feed


class UpdateSingleFeedTests(TestCase):
    """Unit tests for helper `update_single_feed`."""

    def setUp(self):
        """Set up test data."""
        self.feed = Feed.objects.create(
            feed_url="https://example.com/rss.xml",
            name="Example",
            translate_title=False,
            translate_content=False,
            summary=False,
        )

    def _create_feed_with_options(self, **kwargs):
        """Create a feed with specific options."""
        feed = Feed.objects.create(
            feed_url="https://example2.com/rss.xml",
            name="Example2",
            translate_title=False,
            translate_content=False,
            summary=False,
        )
        for key, value in kwargs.items():
            setattr(feed, key, value)
        feed.save()
        return feed

    @patch("core.management.commands.update_feeds.close_old_connections")
    @patch("core.management.commands.update_feeds.handle_feeds_summary")
    @patch("core.management.commands.update_feeds.handle_feeds_translation")
    @patch("core.management.commands.update_feeds.handle_single_feed_fetch")
    def test_update_single_feed_success(
        self, mock_fetch, mock_translate, mock_summary, mock_close_conn
    ):
        """When no internal task raises, should return True and call helpers."""
        feed = self._create_feed_with_options(translate_title=True, summary=True)

        result = cmd.update_single_feed(feed)

        self.assertTrue(result)
        mock_fetch.assert_called_once_with(feed)
        # translate should be called for title only (translate_content False)
        mock_translate.assert_called_once_with([feed], target_field="title")
        mock_summary.assert_called_once_with([feed])
        # close_old_connections should be called twice (entering & finally)
        self.assertGreaterEqual(mock_close_conn.call_count, 2)

    @patch("core.management.commands.update_feeds.logger")
    @patch("core.management.commands.update_feeds.close_old_connections")
    @patch("core.management.commands.update_feeds.handle_single_feed_fetch")
    def test_update_single_feed_exception(
        self, mock_fetch, mock_close_conn, mock_logger
    ):
        """If internal helper raises, function should swallow and return False."""
        mock_fetch.side_effect = RuntimeError("boom")

        result = cmd.update_single_feed(self.feed)

        self.assertFalse(result)
        mock_fetch.assert_called_once_with(self.feed)
        # Verify logger.exception was called
        mock_logger.exception.assert_called_once()
        # Ensure finally block executed
        self.assertGreaterEqual(mock_close_conn.call_count, 2)

    @patch("core.management.commands.update_feeds.close_old_connections")
    @patch("core.management.commands.update_feeds.handle_single_feed_fetch")
    @patch("core.management.commands.update_feeds.logger")
    def test_update_single_feed_feed_not_exist(
        self, mock_logger, mock_fetch, mock_close_conn
    ):
        """Test handling of Feed.DoesNotExist exception."""
        mock_fetch.side_effect = Feed.DoesNotExist("Feed not found")

        result = cmd.update_single_feed(self.feed)

        self.assertFalse(result)
        mock_logger.error.assert_called_once_with(
            f"Feed not found: ID {self.feed.name}"
        )
        mock_close_conn.assert_called()

    @patch("core.management.commands.update_feeds.close_old_connections")
    @patch("core.management.commands.update_feeds.handle_single_feed_fetch")
    @patch("core.management.commands.update_feeds.logger")
    def test_update_single_feed_no_translation_or_summary(
        self, mock_logger, mock_fetch, mock_close_conn
    ):
        """Test feed update without translation or summary."""
        result = cmd.update_single_feed(self.feed)

        self.assertTrue(result)
        mock_fetch.assert_called_once_with(self.feed)
        mock_logger.info.assert_any_call(f"Starting feed update: {self.feed.name}")
        mock_logger.info.assert_any_call(f"Completed feed update: {self.feed.name}")
        mock_close_conn.assert_called()

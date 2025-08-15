from django.test import SimpleTestCase
from unittest import mock
from concurrent.futures import Future
import os
import tempfile

from core.management.commands import update_feeds as cmd
from core.models import Feed, Tag


class UpdateFeedsCommandTests(SimpleTestCase):
    """Unit tests for helper functions inside update_feeds.py (excluding threading)."""

    @mock.patch("core.management.commands.update_feeds.update_multiple_feeds")
    @mock.patch("core.management.commands.update_feeds.Feed")
    def test_update_feeds_for_frequency_success(
        self, mock_feed_model, mock_update_multi
    ):
        """When given a valid frequency, function should call update_multiple_feeds with list."""
        # Mock queryset filter/iterator to yield empty list
        mock_feed_model.objects.filter.return_value.iterator.return_value = []

        cmd.update_feeds_for_frequency("5 min")

        mock_feed_model.objects.filter.assert_called_once_with(update_frequency=5)
        mock_update_multi.assert_called_once_with([])

    @mock.patch("core.management.commands.update_feeds.update_multiple_feeds")
    @mock.patch("core.management.commands.update_feeds.Feed")
    def test_update_feeds_for_frequency_with_feeds(
        self, mock_feed_model, mock_update_multi
    ):
        """Test with actual feeds returned from database."""
        # Create mock feeds
        mock_feed1 = mock.Mock(spec=Feed)
        mock_feed1.name = "Feed 1"
        mock_feed2 = mock.Mock(spec=Feed)
        mock_feed2.name = "Feed 2"

        mock_feed_model.objects.filter.return_value.iterator.return_value = [
            mock_feed1,
            mock_feed2,
        ]

        cmd.update_feeds_for_frequency("hourly")

        mock_feed_model.objects.filter.assert_called_once_with(update_frequency=60)
        mock_update_multi.assert_called_once_with([mock_feed1, mock_feed2])

    @mock.patch("core.management.commands.update_feeds.logger")
    def test_update_feeds_for_frequency_invalid(self, mock_logger):
        """Invalid frequency should not raise but log error."""
        # Function should handle invalid key internally without exception
        cmd.update_feeds_for_frequency("2 hours")  # Not in map -> KeyError handled

        mock_logger.error.assert_called_once_with("Invalid frequency: 2 hours")

    @mock.patch("core.management.commands.update_feeds.logger")
    @mock.patch("core.management.commands.update_feeds.Feed")
    def test_update_feeds_for_frequency_exception(self, mock_feed_model, mock_logger):
        """Test exception handling in update_feeds_for_frequency."""
        # Mock Feed.objects.filter to raise an exception
        mock_feed_model.objects.filter.side_effect = Exception("Database error")

        cmd.update_feeds_for_frequency("5 min")

        mock_logger.exception.assert_called_once()
        # Check that the log message contains the expected content
        call_args = mock_logger.exception.call_args[0][0]
        self.assertIn("Command update_feeds_for_frequency 5 min", call_args)
        self.assertIn("Database error", call_args)

    def test_update_feeds_for_frequency_all_valid_frequencies(self):
        """Test all valid frequency mappings."""
        expected_mappings = {
            "5 min": 5,
            "15 min": 15,
            "30 min": 30,
            "hourly": 60,
            "daily": 1440,
            "weekly": 10080,
        }

        with (
            mock.patch("core.management.commands.update_feeds.Feed") as mock_feed_model,
            mock.patch(
                "core.management.commands.update_feeds.update_multiple_feeds"
            ) as mock_update_multi,
        ):
            mock_feed_model.objects.filter.return_value.iterator.return_value = []

            for freq_str, freq_val in expected_mappings.items():
                with self.subTest(frequency=freq_str):
                    cmd.update_feeds_for_frequency(freq_str)
                    mock_feed_model.objects.filter.assert_called_with(
                        update_frequency=freq_val
                    )


class UpdateSingleFeedTests(SimpleTestCase):
    """Tests for update_single_feed function."""

    def setUp(self):
        self.mock_feed = mock.Mock(spec=Feed)
        self.mock_feed.name = "Test Feed"
        self.mock_feed.translate_title = False
        self.mock_feed.translate_content = False
        self.mock_feed.summary = False

    @mock.patch("core.management.commands.update_feeds.close_old_connections")
    @mock.patch("core.management.commands.update_feeds.handle_single_feed_fetch")
    @mock.patch("core.management.commands.update_feeds.logger")
    def test_update_single_feed_success_no_translation(
        self, mock_logger, mock_fetch, mock_close_conn
    ):
        """Test successful feed update without translation or summary."""
        result = cmd.update_single_feed(self.mock_feed)

        self.assertTrue(result)
        mock_close_conn.assert_called()
        mock_fetch.assert_called_once_with(self.mock_feed)
        mock_logger.info.assert_any_call("Starting feed update: Test Feed")
        mock_logger.info.assert_any_call("Completed feed update: Test Feed")

    @mock.patch("core.management.commands.update_feeds.close_old_connections")
    @mock.patch("core.management.commands.update_feeds.handle_single_feed_fetch")
    @mock.patch("core.management.commands.update_feeds.handle_feeds_translation")
    @mock.patch("core.management.commands.update_feeds.logger")
    def test_update_single_feed_with_title_translation(
        self, mock_logger, mock_translation, mock_fetch, mock_close_conn
    ):
        """Test feed update with title translation enabled."""
        self.mock_feed.translate_title = True

        result = cmd.update_single_feed(self.mock_feed)

        self.assertTrue(result)
        mock_fetch.assert_called_once_with(self.mock_feed)
        mock_translation.assert_called_once_with([self.mock_feed], target_field="title")

    @mock.patch("core.management.commands.update_feeds.close_old_connections")
    @mock.patch("core.management.commands.update_feeds.handle_single_feed_fetch")
    @mock.patch("core.management.commands.update_feeds.handle_feeds_translation")
    @mock.patch("core.management.commands.update_feeds.logger")
    def test_update_single_feed_with_content_translation(
        self, mock_logger, mock_translation, mock_fetch, mock_close_conn
    ):
        """Test feed update with content translation enabled."""
        self.mock_feed.translate_content = True

        result = cmd.update_single_feed(self.mock_feed)

        self.assertTrue(result)
        mock_fetch.assert_called_once_with(self.mock_feed)
        mock_translation.assert_called_once_with(
            [self.mock_feed], target_field="content"
        )

    @mock.patch("core.management.commands.update_feeds.close_old_connections")
    @mock.patch("core.management.commands.update_feeds.handle_single_feed_fetch")
    @mock.patch("core.management.commands.update_feeds.handle_feeds_translation")
    @mock.patch("core.management.commands.update_feeds.handle_feeds_summary")
    @mock.patch("core.management.commands.update_feeds.logger")
    def test_update_single_feed_with_all_features(
        self, mock_logger, mock_summary, mock_translation, mock_fetch, mock_close_conn
    ):
        """Test feed update with all features enabled."""
        self.mock_feed.translate_title = True
        self.mock_feed.translate_content = True
        self.mock_feed.summary = True

        result = cmd.update_single_feed(self.mock_feed)

        self.assertTrue(result)
        mock_fetch.assert_called_once_with(self.mock_feed)
        # Should call translation twice (title and content)
        self.assertEqual(mock_translation.call_count, 2)
        mock_translation.assert_any_call([self.mock_feed], target_field="title")
        mock_translation.assert_any_call([self.mock_feed], target_field="content")
        mock_summary.assert_called_once_with([self.mock_feed])

    @mock.patch("core.management.commands.update_feeds.close_old_connections")
    @mock.patch("core.management.commands.update_feeds.handle_single_feed_fetch")
    @mock.patch("core.management.commands.update_feeds.logger")
    def test_update_single_feed_feed_not_exist(
        self, mock_logger, mock_fetch, mock_close_conn
    ):
        """Test handling of Feed.DoesNotExist exception."""
        mock_fetch.side_effect = Feed.DoesNotExist("Feed not found")

        result = cmd.update_single_feed(self.mock_feed)

        self.assertFalse(result)
        mock_logger.error.assert_called_once_with("Feed not found: ID Test Feed")
        mock_close_conn.assert_called()

    @mock.patch("core.management.commands.update_feeds.close_old_connections")
    @mock.patch("core.management.commands.update_feeds.handle_single_feed_fetch")
    @mock.patch("core.management.commands.update_feeds.logger")
    def test_update_single_feed_general_exception(
        self, mock_logger, mock_fetch, mock_close_conn
    ):
        """Test handling of general exceptions."""
        mock_fetch.side_effect = Exception("Network error")

        result = cmd.update_single_feed(self.mock_feed)

        self.assertFalse(result)
        mock_logger.exception.assert_called_once_with(
            "Error updating feed ID Test Feed: Network error"
        )
        mock_close_conn.assert_called()


class UpdateMultipleFeedsTests(SimpleTestCase):
    """Tests for update_multiple_feeds function."""

    def setUp(self):
        self.mock_feed1 = mock.Mock(spec=Feed)
        self.mock_feed1.name = "Feed 1"
        self.mock_feed1.slug = "feed-1"
        self.mock_feed1.tags.values_list.return_value = [1, 2]

        self.mock_feed2 = mock.Mock(spec=Feed)
        self.mock_feed2.name = "Feed 2"
        self.mock_feed2.slug = "feed-2"
        self.mock_feed2.tags.values_list.return_value = [2, 3]

    @mock.patch("core.management.commands.update_feeds.logger")
    def test_update_multiple_feeds_empty_list(self, mock_logger):
        """Test with empty feeds list."""
        cmd.update_multiple_feeds([])

        mock_logger.info.assert_called_once_with("No feeds to update.")

    @mock.patch("core.management.commands.update_feeds.cache_tag")
    @mock.patch("core.management.commands.update_feeds.cache_rss")
    @mock.patch("core.management.commands.update_feeds.Tag")
    @mock.patch("core.management.commands.update_feeds.wait")
    @mock.patch("core.management.commands.update_feeds.task_manager")
    @mock.patch("core.management.commands.update_feeds.logger")
    def test_update_multiple_feeds_success(
        self,
        mock_logger,
        mock_task_manager,
        mock_wait,
        mock_tag_model,
        mock_cache_rss,
        mock_cache_tag,
    ):
        """Test successful update of multiple feeds."""
        # Mock successful futures
        mock_future1 = mock.Mock(spec=Future)
        mock_future1.result.return_value = True
        mock_future2 = mock.Mock(spec=Future)
        mock_future2.result.return_value = True

        mock_task_manager.submit_task.side_effect = [mock_future1, mock_future2]
        mock_wait.return_value = ([mock_future1, mock_future2], [])

        # Mock tags
        mock_tag1 = mock.Mock(spec=Tag)
        mock_tag1.slug = "tag-1"
        mock_tag2 = mock.Mock(spec=Tag)
        mock_tag2.slug = "tag-2"
        mock_tag_model.objects.filter.return_value = [mock_tag1, mock_tag2]

        feeds = [self.mock_feed1, self.mock_feed2]
        cmd.update_multiple_feeds(feeds)

        # Verify task submission
        self.assertEqual(mock_task_manager.submit_task.call_count, 2)
        mock_task_manager.submit_task.assert_any_call(
            "update_feed_Feed 1", cmd.update_single_feed, self.mock_feed1
        )
        mock_task_manager.submit_task.assert_any_call(
            "update_feed_Feed 2", cmd.update_single_feed, self.mock_feed2
        )

        # Verify wait was called with timeout
        mock_wait.assert_called_once_with([mock_future1, mock_future2], timeout=1800)

        # Verify RSS caching for each feed
        expected_cache_calls = [
            mock.call("feed-1", feed_type="o", format="xml"),
            mock.call("feed-1", feed_type="o", format="json"),
            mock.call("feed-1", feed_type="t", format="xml"),
            mock.call("feed-1", feed_type="t", format="json"),
            mock.call("feed-2", feed_type="o", format="xml"),
            mock.call("feed-2", feed_type="o", format="json"),
            mock.call("feed-2", feed_type="t", format="xml"),
            mock.call("feed-2", feed_type="t", format="json"),
        ]
        mock_cache_rss.assert_has_calls(expected_cache_calls, any_order=True)

        # Verify tag caching
        expected_tag_calls = [
            mock.call("tag-1", feed_type="o", format="xml"),
            mock.call("tag-1", feed_type="t", format="xml"),
            mock.call("tag-1", feed_type="t", format="json"),
            mock.call("tag-2", feed_type="o", format="xml"),
            mock.call("tag-2", feed_type="t", format="xml"),
            mock.call("tag-2", feed_type="t", format="json"),
        ]
        mock_cache_tag.assert_has_calls(expected_tag_calls, any_order=True)

    @mock.patch("core.management.commands.update_feeds.wait")
    @mock.patch("core.management.commands.update_feeds.task_manager")
    @mock.patch("core.management.commands.update_feeds.logger")
    def test_update_multiple_feeds_timeout(
        self, mock_logger, mock_task_manager, mock_wait
    ):
        """Test handling of task timeout."""
        mock_future1 = mock.Mock(spec=Future)
        mock_future2 = mock.Mock(spec=Future)

        mock_task_manager.submit_task.side_effect = [mock_future1, mock_future2]
        # Simulate timeout - some tasks not done
        mock_wait.return_value = ([mock_future1], [mock_future2])

        feeds = [self.mock_feed1, self.mock_feed2]
        cmd.update_multiple_feeds(feeds)

        mock_logger.warning.assert_called_once_with(
            "Feed update task timed out. 1 tasks did not complete."
        )

    @mock.patch("core.management.commands.update_feeds.cache_rss")
    @mock.patch("core.management.commands.update_feeds.wait")
    @mock.patch("core.management.commands.update_feeds.task_manager")
    @mock.patch("core.management.commands.update_feeds.logger")
    def test_update_multiple_feeds_task_exception(
        self, mock_logger, mock_task_manager, mock_wait, mock_cache_rss
    ):
        """Test handling of task exceptions."""
        mock_future1 = mock.Mock(spec=Future)
        mock_future1.result.side_effect = Exception("Task failed")

        mock_task_manager.submit_task.return_value = mock_future1
        mock_wait.return_value = ([mock_future1], [])

        feeds = [self.mock_feed1]
        cmd.update_multiple_feeds(feeds)

        mock_logger.warning.assert_called_once_with(
            "A feed update task resulted in an exception: Task failed"
        )

    @mock.patch("core.management.commands.update_feeds.cache_rss")
    @mock.patch("core.management.commands.update_feeds.Tag")
    @mock.patch("core.management.commands.update_feeds.wait")
    @mock.patch("core.management.commands.update_feeds.task_manager")
    @mock.patch("core.management.commands.update_feeds.logger")
    def test_update_multiple_feeds_cache_exception(
        self, mock_logger, mock_task_manager, mock_wait, mock_tag_model, mock_cache_rss
    ):
        """Test handling of caching exceptions."""
        mock_future1 = mock.Mock(spec=Future)
        mock_future1.result.return_value = True

        mock_task_manager.submit_task.return_value = mock_future1
        mock_wait.return_value = ([mock_future1], [])
        mock_cache_rss.side_effect = Exception("Cache error")
        mock_tag_model.objects.filter.return_value = []

        feeds = [self.mock_feed1]
        cmd.update_multiple_feeds(feeds)

        # Should log cache error
        mock_logger.error.assert_called()
        error_call = mock_logger.error.call_args[0][0]
        self.assertIn("Failed to cache RSS for feed-1", error_call)
        self.assertIn("Cache error", error_call)

    @mock.patch("core.management.commands.update_feeds.logger")
    def test_update_multiple_feeds_general_exception(self, mock_logger):
        """Test handling of general exceptions in update_multiple_feeds."""
        with mock.patch(
            "core.management.commands.update_feeds.task_manager"
        ) as mock_task_manager:
            mock_task_manager.submit_task.side_effect = Exception("General error")

            feeds = [self.mock_feed1]
            cmd.update_multiple_feeds(feeds)

            mock_logger.exception.assert_called_once_with(
                "Command update_multiple_feeds failed: %s", "General error"
            )

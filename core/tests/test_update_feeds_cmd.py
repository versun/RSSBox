from django.test import SimpleTestCase
from unittest.mock import patch, Mock
from concurrent.futures import Future

from core.management.commands import update_feeds as cmd
from core.models import Feed, Tag


class UpdateFeedsCommandTests(SimpleTestCase):
    """Unit tests for helper functions inside update_feeds.py."""

    def setUp(self):
        """Set up common test data."""
        self.frequency_mappings = {
            "5 min": 5,
            "15 min": 15,
            "30 min": 30,
            "hourly": 60,
            "daily": 1440,
            "weekly": 10080,
        }

    @patch("core.management.commands.update_feeds.update_multiple_feeds")
    @patch("core.management.commands.update_feeds.Feed")
    def test_update_feeds_for_frequency_success(
        self, mock_feed_model, mock_update_multi
    ):
        """Test successful feed update for frequency."""
        mock_feed_model.objects.filter.return_value.iterator.return_value = []

        cmd.update_feeds_for_frequency("5 min")

        mock_feed_model.objects.filter.assert_called_once_with(update_frequency=5)
        mock_update_multi.assert_called_once_with([])

    @patch("core.management.commands.update_feeds.update_multiple_feeds")
    @patch("core.management.commands.update_feeds.Feed")
    def test_update_feeds_for_frequency_with_feeds(
        self, mock_feed_model, mock_update_multi
    ):
        """Test feed update with actual feeds returned from database."""
        mock_feed1 = Mock(spec=Feed, name="Feed 1")
        mock_feed2 = Mock(spec=Feed, name="Feed 2")

        mock_feed_model.objects.filter.return_value.iterator.return_value = [
            mock_feed1,
            mock_feed2,
        ]

        cmd.update_feeds_for_frequency("hourly")

        mock_feed_model.objects.filter.assert_called_once_with(update_frequency=60)
        mock_update_multi.assert_called_once_with([mock_feed1, mock_feed2])

    @patch("core.management.commands.update_feeds.logger")
    def test_update_feeds_for_frequency_invalid(self, mock_logger):
        """Test invalid frequency handling."""
        cmd.update_feeds_for_frequency("2 hours")

        mock_logger.error.assert_called_once_with("Invalid frequency: 2 hours")

    @patch("core.management.commands.update_feeds.logger")
    @patch("core.management.commands.update_feeds.Feed")
    def test_update_feeds_for_frequency_exception(self, mock_feed_model, mock_logger):
        """Test exception handling in update_feeds_for_frequency."""
        mock_feed_model.objects.filter.side_effect = Exception("Database error")

        cmd.update_feeds_for_frequency("5 min")

        mock_logger.exception.assert_called_once()
        call_args = mock_logger.exception.call_args[0][0]
        self.assertIn("Command update_feeds_for_frequency 5 min", call_args)
        self.assertIn("Database error", call_args)

    def test_update_feeds_for_frequency_all_valid_frequencies(self):
        """Test all valid frequency mappings."""
        with (
            patch("core.management.commands.update_feeds.Feed") as mock_feed_model,
            patch(
                "core.management.commands.update_feeds.update_multiple_feeds"
            ) as mock_update_multi,
        ):
            mock_feed_model.objects.filter.return_value.iterator.return_value = []

            for freq_str, freq_val in self.frequency_mappings.items():
                with self.subTest(frequency=freq_str):
                    cmd.update_feeds_for_frequency(freq_str)
                    mock_feed_model.objects.filter.assert_called_with(
                        update_frequency=freq_val
                    )


class UpdateSingleFeedTests(SimpleTestCase):
    """Tests for update_single_feed function."""

    def setUp(self):
        """Set up test data."""
        self.mock_feed = Mock(spec=Feed)
        self.mock_feed.name = "Test Feed"
        self.mock_feed.translate_title = False
        self.mock_feed.translate_content = False
        self.mock_feed.summary = False

    @patch("core.management.commands.update_feeds.close_old_connections")
    @patch("core.management.commands.update_feeds.handle_single_feed_fetch")
    @patch("core.management.commands.update_feeds.logger")
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

    @patch("core.management.commands.update_feeds.close_old_connections")
    @patch("core.management.commands.update_feeds.handle_single_feed_fetch")
    @patch("core.management.commands.update_feeds.handle_feeds_translation")
    @patch("core.management.commands.update_feeds.logger")
    def test_update_single_feed_with_title_translation(
        self, mock_logger, mock_translation, mock_fetch, mock_close_conn
    ):
        """Test feed update with title translation enabled."""
        self.mock_feed.translate_title = True

        result = cmd.update_single_feed(self.mock_feed)

        self.assertTrue(result)
        mock_fetch.assert_called_once_with(self.mock_feed)
        mock_translation.assert_called_once_with([self.mock_feed], target_field="title")

    @patch("core.management.commands.update_feeds.close_old_connections")
    @patch("core.management.commands.update_feeds.handle_single_feed_fetch")
    @patch("core.management.commands.update_feeds.handle_feeds_translation")
    @patch("core.management.commands.update_feeds.logger")
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

    @patch("core.management.commands.update_feeds.close_old_connections")
    @patch("core.management.commands.update_feeds.handle_single_feed_fetch")
    @patch("core.management.commands.update_feeds.handle_feeds_translation")
    @patch("core.management.commands.update_feeds.handle_feeds_summary")
    @patch("core.management.commands.update_feeds.logger")
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
        self.assertEqual(mock_translation.call_count, 2)
        mock_translation.assert_any_call([self.mock_feed], target_field="title")
        mock_translation.assert_any_call([self.mock_feed], target_field="content")
        mock_summary.assert_called_once_with([self.mock_feed])

    @patch("core.management.commands.update_feeds.close_old_connections")
    @patch("core.management.commands.update_feeds.handle_single_feed_fetch")
    @patch("core.management.commands.update_feeds.logger")
    def test_update_single_feed_feed_not_exist(
        self, mock_logger, mock_fetch, mock_close_conn
    ):
        """Test handling of Feed.DoesNotExist exception."""
        mock_fetch.side_effect = Feed.DoesNotExist("Feed not found")

        result = cmd.update_single_feed(self.mock_feed)

        self.assertFalse(result)
        mock_logger.error.assert_called_once_with("Feed not found: ID Test Feed")
        mock_close_conn.assert_called()

    @patch("core.management.commands.update_feeds.close_old_connections")
    @patch("core.management.commands.update_feeds.handle_single_feed_fetch")
    @patch("core.management.commands.update_feeds.logger")
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
        """Set up test data."""
        self.mock_feed1 = Mock(spec=Feed)
        self.mock_feed1.name = "Feed 1"
        self.mock_feed1.slug = "feed-1"
        self.mock_feed1.tags.values_list.return_value = [1, 2]

        self.mock_feed2 = Mock(spec=Feed)
        self.mock_feed2.name = "Feed 2"
        self.mock_feed2.slug = "feed-2"
        self.mock_feed2.tags.values_list.return_value = [2, 3]

    @patch("core.management.commands.update_feeds.logger")
    def test_update_multiple_feeds_empty_list(self, mock_logger):
        """Test with empty feeds list."""
        cmd.update_multiple_feeds([])
        mock_logger.info.assert_called_once_with("No feeds to update.")

    def _create_mock_futures(self, success_count=2):
        """Helper method to create mock futures."""
        futures = []
        for i in range(success_count):
            mock_future = Mock(spec=Future)
            mock_future.result.return_value = True
            futures.append(mock_future)
        return futures

    def _create_mock_tags(self):
        """Helper method to create mock tags."""
        mock_tag1 = Mock(spec=Tag, slug="tag-1")
        mock_tag2 = Mock(spec=Tag, slug="tag-2")
        return [mock_tag1, mock_tag2]

    @patch("core.management.commands.update_feeds.cache_tag")
    @patch("core.management.commands.update_feeds.cache_rss")
    @patch("core.management.commands.update_feeds.Tag")
    @patch("core.management.commands.update_feeds.wait")
    @patch("core.management.commands.update_feeds.task_manager")
    @patch("core.management.commands.update_feeds.logger")
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
        mock_futures = self._create_mock_futures(2)
        mock_task_manager.submit_task.side_effect = mock_futures
        mock_wait.return_value = (mock_futures, [])
        mock_tag_model.objects.filter.return_value = self._create_mock_tags()

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
        mock_wait.assert_called_once_with(mock_futures, timeout=1800)

        # Verify RSS caching for each feed
        expected_rss_calls = [
            ("feed-1", "o", "xml"),
            ("feed-1", "o", "json"),
            ("feed-1", "t", "xml"),
            ("feed-1", "t", "json"),
            ("feed-2", "o", "xml"),
            ("feed-2", "o", "json"),
            ("feed-2", "t", "xml"),
            ("feed-2", "t", "json"),
        ]
        for feed_slug, feed_type, format_type in expected_rss_calls:
            mock_cache_rss.assert_any_call(
                feed_slug, feed_type=feed_type, format=format_type
            )

        # Verify tag caching
        expected_tag_calls = [
            ("tag-1", "o", "xml"),
            ("tag-1", "t", "xml"),
            ("tag-1", "t", "json"),
            ("tag-2", "o", "xml"),
            ("tag-2", "t", "xml"),
            ("tag-2", "t", "json"),
        ]
        for tag_slug, feed_type, format_type in expected_tag_calls:
            mock_cache_tag.assert_any_call(
                tag_slug, feed_type=feed_type, format=format_type
            )

    @patch("core.management.commands.update_feeds.wait")
    @patch("core.management.commands.update_feeds.task_manager")
    @patch("core.management.commands.update_feeds.logger")
    def test_update_multiple_feeds_timeout(
        self, mock_logger, mock_task_manager, mock_wait
    ):
        """Test handling of task timeout."""
        mock_futures = self._create_mock_futures(2)
        mock_task_manager.submit_task.side_effect = mock_futures
        # Simulate timeout - some tasks not done
        mock_wait.return_value = ([mock_futures[0]], [mock_futures[1]])

        feeds = [self.mock_feed1, self.mock_feed2]
        cmd.update_multiple_feeds(feeds)

        mock_logger.warning.assert_called_once_with(
            "Feed update task timed out. 1 tasks did not complete."
        )

    @patch("core.management.commands.update_feeds.cache_rss")
    @patch("core.management.commands.update_feeds.wait")
    @patch("core.management.commands.update_feeds.task_manager")
    @patch("core.management.commands.update_feeds.logger")
    def test_update_multiple_feeds_task_exception(
        self, mock_logger, mock_task_manager, mock_wait, mock_cache_rss
    ):
        """Test handling of task exceptions."""
        mock_future = Mock(spec=Future)
        mock_future.result.side_effect = Exception("Task failed")

        mock_task_manager.submit_task.return_value = mock_future
        mock_wait.return_value = ([mock_future], [])

        feeds = [self.mock_feed1]
        cmd.update_multiple_feeds(feeds)

        mock_logger.warning.assert_called_once_with(
            "A feed update task resulted in an exception: Task failed"
        )

    @patch("core.management.commands.update_feeds.cache_rss")
    @patch("core.management.commands.update_feeds.Tag")
    @patch("core.management.commands.update_feeds.wait")
    @patch("core.management.commands.update_feeds.task_manager")
    @patch("core.management.commands.update_feeds.logger")
    def test_update_multiple_feeds_cache_exception(
        self, mock_logger, mock_task_manager, mock_wait, mock_tag_model, mock_cache_rss
    ):
        """Test handling of caching exceptions."""
        mock_future = Mock(spec=Future)
        mock_future.result.return_value = True

        mock_task_manager.submit_task.return_value = mock_future
        mock_wait.return_value = ([mock_future], [])
        mock_cache_rss.side_effect = Exception("Cache error")
        mock_tag_model.objects.filter.return_value = []

        feeds = [self.mock_feed1]
        cmd.update_multiple_feeds(feeds)

        mock_logger.error.assert_called()
        error_call = mock_logger.error.call_args[0][0]
        self.assertIn("Failed to cache RSS for feed-1", error_call)
        self.assertIn("Cache error", error_call)

    @patch("core.management.commands.update_feeds.logger")
    def test_update_multiple_feeds_general_exception(self, mock_logger):
        """Test handling of general exceptions in update_multiple_feeds."""
        with patch(
            "core.management.commands.update_feeds.task_manager"
        ) as mock_task_manager:
            mock_task_manager.submit_task.side_effect = Exception("General error")

            feeds = [self.mock_feed1]
            cmd.update_multiple_feeds(feeds)

            mock_logger.exception.assert_called_once_with(
                "Command update_multiple_feeds failed: %s", "General error"
            )

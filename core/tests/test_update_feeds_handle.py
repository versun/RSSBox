from unittest import mock
from argparse import ArgumentParser

from django.test import SimpleTestCase

from core.management.commands import update_feeds as cmd


class UpdateFeedsHandleTests(SimpleTestCase):
    """Tests for `Command.handle` function covering edge cases."""

    def setUp(self):
        # Fresh command instance per test
        self.command = cmd.Command()

    def test_handle_invalid_frequency(self):
        """Invalid frequency string should raise SystemExit(1)."""
        with self.assertRaises(SystemExit) as ctx:
            self.command.handle(frequency="2 hours")
        self.assertEqual(ctx.exception.code, 1)

    @mock.patch("core.management.commands.update_feeds.os.remove")
    @mock.patch(
        "core.management.commands.update_feeds.open", new_callable=mock.mock_open
    )
    @mock.patch(
        "core.management.commands.update_feeds.os.path.exists", return_value=True
    )
    def test_handle_lock_file_exists(self, mock_exists, mock_open_file, mock_remove):
        """When lock file present, command should exit with code 0 and not proceed."""
        with self.assertRaises(SystemExit) as ctx:
            self.command.handle(frequency="5 min")
        self.assertEqual(ctx.exception.code, 0)
        # Should not attempt to open lock file or remove it
        mock_open_file.assert_not_called()
        mock_remove.assert_not_called()

    @mock.patch("core.management.commands.update_feeds.os.remove")
    @mock.patch(
        "core.management.commands.update_feeds.open", new_callable=mock.mock_open
    )
    @mock.patch(
        "core.management.commands.update_feeds.os.path.exists",
        side_effect=[False, True],
    )
    @mock.patch("core.management.commands.update_feeds.update_feeds_for_frequency")
    def test_handle_happy_path(
        self, mock_update, mock_exists, mock_open_file, mock_remove
    ):
        """Valid frequency without lock proceeds and cleans up lock file."""
        self.command.handle(frequency="5 min")

        # Lock file written
        mock_open_file.assert_called()
        # Helper called
        mock_update.assert_called_once_with(simple_update_frequency="5 min")
        # Lock file removed in finally block
        mock_remove.assert_called()

    def test_handle_no_frequency_provided(self):
        """When no frequency is provided, should exit with code 1."""
        with self.assertRaises(SystemExit) as ctx:
            self.command.handle(frequency=None)
        self.assertEqual(ctx.exception.code, 1)

    def test_handle_all_valid_frequencies(self):
        """Test that all valid frequencies are accepted."""
        valid_frequencies = ["5 min", "15 min", "30 min", "hourly", "daily", "weekly"]
        
        for frequency in valid_frequencies:
            with self.subTest(frequency=frequency):
                with mock.patch("core.management.commands.update_feeds.os.path.exists", return_value=False), \
                     mock.patch("core.management.commands.update_feeds.open", mock.mock_open()), \
                     mock.patch("core.management.commands.update_feeds.os.remove"), \
                     mock.patch("core.management.commands.update_feeds.update_feeds_for_frequency") as mock_update:
                    
                    self.command.handle(frequency=frequency)
                    mock_update.assert_called_once_with(simple_update_frequency=frequency)

    @mock.patch("core.management.commands.update_feeds.os.remove")
    @mock.patch("core.management.commands.update_feeds.open", new_callable=mock.mock_open)
    @mock.patch("core.management.commands.update_feeds.os.path.exists", side_effect=[False, True])
    @mock.patch("core.management.commands.update_feeds.update_feeds_for_frequency")
    def test_handle_exception_during_update(
        self, mock_update, mock_exists, mock_open_file, mock_remove
    ):
        """Test exception handling during update_feeds_for_frequency."""
        mock_update.side_effect = Exception("Update failed")
        
        with self.assertRaises(SystemExit) as ctx:
            self.command.handle(frequency="5 min")
        
        self.assertEqual(ctx.exception.code, 1)
        # Lock file should still be removed in finally block
        mock_remove.assert_called()

    @mock.patch("core.management.commands.update_feeds.os.remove")
    @mock.patch("core.management.commands.update_feeds.open", new_callable=mock.mock_open)
    @mock.patch("core.management.commands.update_feeds.os.path.exists", side_effect=[False, True])
    @mock.patch("core.management.commands.update_feeds.update_feeds_for_frequency")
    @mock.patch("core.management.commands.update_feeds.logger")
    def test_handle_exception_logging(
        self, mock_logger, mock_update, mock_exists, mock_open_file, mock_remove
    ):
        """Test that exceptions are properly logged."""
        mock_update.side_effect = Exception("Update failed")
        
        with self.assertRaises(SystemExit):
            self.command.handle(frequency="5 min")
        
        mock_logger.exception.assert_called_once_with(
            "Command update_feeds_for_frequency failed: Update failed"
        )

    @mock.patch("core.management.commands.update_feeds.os.remove")
    @mock.patch("core.management.commands.update_feeds.open", new_callable=mock.mock_open)
    @mock.patch("core.management.commands.update_feeds.os.path.exists", return_value=False)
    @mock.patch("core.management.commands.update_feeds.update_feeds_for_frequency")
    def test_handle_lock_file_creation_and_content(
        self, mock_update, mock_exists, mock_open_file, mock_remove
    ):
        """Test that lock file is created with correct content (PID)."""
        with mock.patch("core.management.commands.update_feeds.os.getpid", return_value=12345):
            self.command.handle(frequency="hourly")
        
        # Verify lock file path and content
        expected_lock_path = "/tmp/update_feeds_hourly.lock"
        mock_open_file.assert_called_with(expected_lock_path, "w")
        
        # Verify PID was written to file
        handle = mock_open_file.return_value.__enter__.return_value
        handle.write.assert_called_once_with("12345")

    @mock.patch("core.management.commands.update_feeds.os.remove")
    @mock.patch("core.management.commands.update_feeds.open", new_callable=mock.mock_open)
    @mock.patch("core.management.commands.update_feeds.os.path.exists", side_effect=[False, False])
    @mock.patch("core.management.commands.update_feeds.update_feeds_for_frequency")
    def test_handle_lock_file_cleanup_when_not_exists(
        self, mock_update, mock_exists, mock_open_file, mock_remove
    ):
        """Test that remove is not called if lock file doesn't exist in finally block."""
        self.command.handle(frequency="5 min")
        
        # os.remove should not be called since lock file doesn't exist in finally
        mock_remove.assert_not_called()

    def test_handle_frequency_to_lock_file_mapping(self):
        """Test that frequency strings are correctly converted to lock file names."""
        frequency_to_lock = {
            "5 min": "/tmp/update_feeds_5_min.lock",
            "15 min": "/tmp/update_feeds_15_min.lock", 
            "30 min": "/tmp/update_feeds_30_min.lock",
            "hourly": "/tmp/update_feeds_hourly.lock",
            "daily": "/tmp/update_feeds_daily.lock",
            "weekly": "/tmp/update_feeds_weekly.lock",
        }
        
        for frequency, expected_lock_path in frequency_to_lock.items():
            with self.subTest(frequency=frequency):
                with mock.patch("core.management.commands.update_feeds.os.path.exists", return_value=True) as mock_exists:
                    with self.assertRaises(SystemExit):
                        self.command.handle(frequency=frequency)
                    
                    mock_exists.assert_called_with(expected_lock_path)


class UpdateSingleFeedTests(SimpleTestCase):
    """Tests for update_single_feed function."""

    @mock.patch("core.management.commands.update_feeds.close_old_connections")
    @mock.patch("core.management.commands.update_feeds.handle_single_feed_fetch")
    @mock.patch("core.management.commands.update_feeds.handle_feeds_translation")
    @mock.patch("core.management.commands.update_feeds.handle_feeds_summary")
    @mock.patch("core.management.commands.update_feeds.logger")
    def test_update_single_feed_success_with_all_options(
        self, mock_logger, mock_summary, mock_translation, mock_fetch, mock_close_conn
    ):
        """Test successful feed update with all translation and summary options enabled."""
        # Create mock feed with all options enabled
        mock_feed = mock.Mock()
        mock_feed.name = "Test Feed"
        mock_feed.translate_title = True
        mock_feed.translate_content = True
        mock_feed.summary = True

        result = cmd.update_single_feed(mock_feed)

        self.assertTrue(result)
        mock_close_conn.assert_called()
        mock_fetch.assert_called_once_with(mock_feed)
        
        # Should call translation for both title and content
        expected_calls = [
            mock.call([mock_feed], target_field="title"),
            mock.call([mock_feed], target_field="content")
        ]
        mock_translation.assert_has_calls(expected_calls)
        
        mock_summary.assert_called_once_with([mock_feed])
        mock_logger.info.assert_any_call(f"Starting feed update: {mock_feed.name}")
        mock_logger.info.assert_any_call(f"Completed feed update: {mock_feed.name}")

    @mock.patch("core.management.commands.update_feeds.close_old_connections")
    @mock.patch("core.management.commands.update_feeds.handle_single_feed_fetch")
    @mock.patch("core.management.commands.update_feeds.handle_feeds_translation")
    @mock.patch("core.management.commands.update_feeds.handle_feeds_summary")
    @mock.patch("core.management.commands.update_feeds.logger")
    def test_update_single_feed_success_minimal_options(
        self, mock_logger, mock_summary, mock_translation, mock_fetch, mock_close_conn
    ):
        """Test successful feed update with minimal options (no translation/summary)."""
        mock_feed = mock.Mock()
        mock_feed.name = "Test Feed"
        mock_feed.translate_title = False
        mock_feed.translate_content = False
        mock_feed.summary = False

        result = cmd.update_single_feed(mock_feed)

        self.assertTrue(result)
        mock_fetch.assert_called_once_with(mock_feed)
        mock_translation.assert_not_called()
        mock_summary.assert_not_called()

    @mock.patch("core.management.commands.update_feeds.close_old_connections")
    @mock.patch("core.management.commands.update_feeds.handle_single_feed_fetch")
    @mock.patch("core.management.commands.update_feeds.logger")
    def test_update_single_feed_fetch_exception(
        self, mock_logger, mock_fetch, mock_close_conn
    ):
        """Test feed update when fetch raises an exception."""
        mock_feed = mock.Mock()
        mock_feed.name = "Test Feed"
        mock_feed.translate_title = False
        mock_feed.translate_content = False
        mock_feed.summary = False
        
        mock_fetch.side_effect = Exception("Fetch failed")

        result = cmd.update_single_feed(mock_feed)

        self.assertFalse(result)
        mock_logger.exception.assert_called_once_with(
            f"Error updating feed ID {mock_feed.name}: Fetch failed"
        )
        mock_close_conn.assert_called()

    @mock.patch("core.management.commands.update_feeds.close_old_connections")
    @mock.patch("core.management.commands.update_feeds.handle_single_feed_fetch")
    @mock.patch("core.management.commands.update_feeds.logger")
    def test_update_single_feed_does_not_exist(
        self, mock_logger, mock_fetch, mock_close_conn
    ):
        """Test feed update when Feed.DoesNotExist is raised."""
        from core.models import Feed
        
        mock_feed = mock.Mock()
        mock_feed.name = "Test Feed"
        mock_feed.translate_title = False
        mock_feed.translate_content = False
        mock_feed.summary = False
        
        mock_fetch.side_effect = Feed.DoesNotExist("Feed not found")

        result = cmd.update_single_feed(mock_feed)

        self.assertFalse(result)
        mock_logger.error.assert_called_once_with(f"Feed not found: ID {mock_feed.name}")
        mock_close_conn.assert_called()


class UpdateMultipleFeedsTests(SimpleTestCase):
    """Tests for update_multiple_feeds function."""

    @mock.patch("core.management.commands.update_feeds.logger")
    def test_update_multiple_feeds_empty_list(self, mock_logger):
        """Test update_multiple_feeds with empty feed list."""
        cmd.update_multiple_feeds([])
        
        mock_logger.info.assert_called_once_with("No feeds to update.")

    @mock.patch("core.management.commands.update_feeds.task_manager")
    @mock.patch("core.management.commands.update_feeds.wait")
    @mock.patch("core.management.commands.update_feeds.cache_rss")
    @mock.patch("core.management.commands.update_feeds.cache_tag")
    @mock.patch("core.management.commands.update_feeds.Tag")
    @mock.patch("core.management.commands.update_feeds.logger")
    def test_update_multiple_feeds_success(
        self, mock_logger, mock_tag_model, mock_cache_tag, mock_cache_rss, 
        mock_wait, mock_task_manager
    ):
        """Test successful update of multiple feeds with caching."""
        # Create mock feeds
        mock_feed1 = mock.Mock()
        mock_feed1.name = "Feed 1"
        mock_feed1.slug = "feed-1"
        mock_feed1.tags.values_list.return_value = [1, 2]
        
        mock_feed2 = mock.Mock()
        mock_feed2.name = "Feed 2"
        mock_feed2.slug = "feed-2"
        mock_feed2.tags.values_list.return_value = [2, 3]
        
        feeds = [mock_feed1, mock_feed2]
        
        # Mock task manager
        mock_future1 = mock.Mock()
        mock_future2 = mock.Mock()
        mock_task_manager.submit_task.side_effect = [mock_future1, mock_future2]
        
        # Mock wait function - all tasks complete successfully
        mock_wait.return_value = ([mock_future1, mock_future2], [])
        
        # Mock tags
        mock_tag1 = mock.Mock()
        mock_tag1.slug = "tag-1"
        mock_tag2 = mock.Mock()
        mock_tag2.slug = "tag-2"
        mock_tag_model.objects.filter.return_value = [mock_tag1, mock_tag2]

        cmd.update_multiple_feeds(feeds)

        # Verify task submission
        self.assertEqual(mock_task_manager.submit_task.call_count, 2)
        mock_task_manager.submit_task.assert_any_call(
            "update_feed_Feed 1", cmd.update_single_feed, mock_feed1
        )
        mock_task_manager.submit_task.assert_any_call(
            "update_feed_Feed 2", cmd.update_single_feed, mock_feed2
        )
        
        # Verify wait call
        mock_wait.assert_called_once_with([mock_future1, mock_future2], timeout=1800)
        
        # Verify RSS caching for each feed
        expected_rss_calls = []
        for feed in feeds:
            for feed_type in ["o", "t"]:
                for format_type in ["xml", "json"]:
                    expected_rss_calls.append(mock.call(feed.slug, feed_type=feed_type, format=format_type))
        
        mock_cache_rss.assert_has_calls(expected_rss_calls, any_order=True)
        
        # Verify tag caching
        expected_tag_calls = []
        for tag in [mock_tag1, mock_tag2]:
            expected_tag_calls.extend([
                mock.call(tag.slug, feed_type="o", format="xml"),
                mock.call(tag.slug, feed_type="t", format="xml"),
                mock.call(tag.slug, feed_type="t", format="json")
            ])
        
        mock_cache_tag.assert_has_calls(expected_tag_calls, any_order=True)

    @mock.patch("core.management.commands.update_feeds.task_manager")
    @mock.patch("core.management.commands.update_feeds.wait")
    @mock.patch("core.management.commands.update_feeds.logger")
    def test_update_multiple_feeds_timeout(
        self, mock_logger, mock_wait, mock_task_manager
    ):
        """Test update_multiple_feeds when some tasks timeout."""
        mock_feed = mock.Mock()
        mock_feed.name = "Feed 1"
        mock_feed.slug = "feed-1"
        mock_feed.tags.values_list.return_value = []
        
        mock_future1 = mock.Mock()
        mock_future2 = mock.Mock()
        mock_task_manager.submit_task.return_value = mock_future1
        
        # Mock timeout scenario
        mock_wait.return_value = ([mock_future1], [mock_future2])

        cmd.update_multiple_feeds([mock_feed])

        mock_logger.warning.assert_called_once_with(
            "Feed update task timed out. 1 tasks did not complete."
        )

    @mock.patch("core.management.commands.update_feeds.task_manager")
    @mock.patch("core.management.commands.update_feeds.wait")
    @mock.patch("core.management.commands.update_feeds.cache_rss")
    @mock.patch("core.management.commands.update_feeds.logger")
    def test_update_multiple_feeds_task_exception(
        self, mock_logger, mock_cache_rss, mock_wait, mock_task_manager
    ):
        """Test update_multiple_feeds when a task raises an exception."""
        mock_feed = mock.Mock()
        mock_feed.name = "Feed 1"
        mock_feed.slug = "feed-1"
        mock_feed.tags.values_list.return_value = []
        
        mock_future = mock.Mock()
        mock_future.result.side_effect = Exception("Task failed")
        mock_task_manager.submit_task.return_value = mock_future
        
        mock_wait.return_value = ([mock_future], [])

        cmd.update_multiple_feeds([mock_feed])

        mock_logger.warning.assert_called_once_with(
            "A feed update task resulted in an exception: Task failed"
        )

    @mock.patch("core.management.commands.update_feeds.task_manager")
    @mock.patch("core.management.commands.update_feeds.wait")
    @mock.patch("core.management.commands.update_feeds.cache_rss")
    @mock.patch("core.management.commands.update_feeds.logger")
    def test_update_multiple_feeds_cache_exception(
        self, mock_logger, mock_cache_rss, mock_wait, mock_task_manager
    ):
        """Test update_multiple_feeds when RSS caching fails."""
        mock_feed = mock.Mock()
        mock_feed.name = "Feed 1"
        mock_feed.slug = "feed-1"
        mock_feed.tags.values_list.return_value = []
        
        mock_future = mock.Mock()
        mock_task_manager.submit_task.return_value = mock_future
        mock_wait.return_value = ([mock_future], [])
        
        # First cache call succeeds, second fails
        mock_cache_rss.side_effect = [None, Exception("Cache failed"), None, None]

        with mock.patch("core.management.commands.update_feeds.time.time", return_value=1234567890):
            cmd.update_multiple_feeds([mock_feed])

        mock_logger.error.assert_called_with(
            f"1234567890: Failed to cache RSS for {mock_feed.slug}: Cache failed"
        )

    @mock.patch("core.management.commands.update_feeds.task_manager")
    @mock.patch("core.management.commands.update_feeds.logger")
    def test_update_multiple_feeds_general_exception(
        self, mock_logger, mock_task_manager
    ):
        """Test update_multiple_feeds when a general exception occurs."""
        mock_feed = mock.Mock()
        mock_feed.name = "Feed 1"
        
        mock_task_manager.submit_task.side_effect = Exception("General error")

        cmd.update_multiple_feeds([mock_feed])

        mock_logger.exception.assert_called_once_with(
            "Command update_multiple_feeds failed: %s", "General error"
        )

    @mock.patch("core.management.commands.update_feeds.task_manager")
    @mock.patch("core.management.commands.update_feeds.wait")
    @mock.patch("core.management.commands.update_feeds.cache_rss")
    @mock.patch("core.management.commands.update_feeds.cache_tag")
    @mock.patch("core.management.commands.update_feeds.Tag")
    @mock.patch("core.management.commands.update_feeds.logger")
    def test_update_multiple_feeds_cache_tag_exception(
        self, mock_logger, mock_tag_model, mock_cache_tag, mock_cache_rss, 
        mock_wait, mock_task_manager
    ):
        """Test update_multiple_feeds when tag caching fails - covers lines 173-174."""
        # Create mock feed with tags
        mock_feed = mock.Mock()
        mock_feed.name = "Feed 1"
        mock_feed.slug = "feed-1"
        mock_feed.tags.values_list.return_value = [1, 2]
        
        # Mock task completion
        mock_future = mock.Mock()
        mock_task_manager.submit_task.return_value = mock_future
        mock_wait.return_value = ([mock_future], [])
        
        # Mock tags
        mock_tag1 = mock.Mock()
        mock_tag1.slug = "tech-news"
        mock_tag2 = mock.Mock() 
        mock_tag2.slug = "ai-updates"
        mock_tag_model.objects.filter.return_value = [mock_tag1, mock_tag2]
        
        # RSS caching succeeds, but tag caching fails for second tag
        # For tag1: 3 successful calls, For tag2: first call fails 
        mock_cache_tag.side_effect = [
            None,  # tag1: cache_tag(tech-news, feed_type="o", format="xml") 
            None,  # tag1: cache_tag(tech-news, feed_type="t", format="xml")
            None,  # tag1: cache_tag(tech-news, feed_type="t", format="json") 
            Exception("Tag cache failed"),  # tag2: cache_tag(ai-updates, feed_type="o", format="xml") fails
        ]

        cmd.update_multiple_feeds([mock_feed])

        # Verify that the error was logged with the correct format (line 174)
        # The exception occurs on the second tag (ai-updates)
        mock_logger.error.assert_called_with(
            f"Failed to cache tag {mock_tag2.slug}: Tag cache failed"
        )


class UpdateFeedsForFrequencyTests(SimpleTestCase):
    """Tests for update_feeds_for_frequency function."""

    @mock.patch("core.management.commands.update_feeds.Feed")
    @mock.patch("core.management.commands.update_feeds.update_multiple_feeds")
    @mock.patch("core.management.commands.update_feeds.logger")
    def test_update_feeds_for_frequency_success(
        self, mock_logger, mock_update_multiple, mock_feed_model
    ):
        """Test successful update_feeds_for_frequency call."""
        # Mock feeds
        mock_feed1 = mock.Mock()
        mock_feed2 = mock.Mock()
        mock_feeds_iterator = iter([mock_feed1, mock_feed2])
        
        mock_feed_model.objects.filter.return_value.iterator.return_value = mock_feeds_iterator

        with mock.patch("core.management.commands.update_feeds.current_time", "2024-01-01 12:00:00"):
            cmd.update_feeds_for_frequency("hourly")

        # Verify database query
        mock_feed_model.objects.filter.assert_called_once_with(update_frequency=60)
        
        # Verify logging
        mock_logger.info.assert_called_once_with(
            "2024-01-01 12:00:00: Start update feeds for frequency: hourly, feeds count: 2"
        )
        
        # Verify update_multiple_feeds called with correct feeds
        mock_update_multiple.assert_called_once()
        args = mock_update_multiple.call_args[0][0]
        self.assertEqual(len(args), 2)

    @mock.patch("core.management.commands.update_feeds.logger")
    def test_update_feeds_for_frequency_invalid_frequency(self, mock_logger):
        """Test update_feeds_for_frequency with invalid frequency."""
        cmd.update_feeds_for_frequency("invalid")
        
        mock_logger.error.assert_called_once_with("Invalid frequency: invalid")

    @mock.patch("core.management.commands.update_feeds.Feed")
    @mock.patch("core.management.commands.update_feeds.update_multiple_feeds")
    @mock.patch("core.management.commands.update_feeds.logger")
    def test_update_feeds_for_frequency_exception(
        self, mock_logger, mock_update_multiple, mock_feed_model
    ):
        """Test update_feeds_for_frequency when an exception occurs."""
        mock_feed_model.objects.filter.side_effect = Exception("Database error")

        with mock.patch("core.management.commands.update_feeds.current_time", "2024-01-01 12:00:00"):
            cmd.update_feeds_for_frequency("daily")

        mock_logger.exception.assert_called_once_with(
            "2024-01-01 12:00:00: Command update_feeds_for_frequency daily: Database error"
        )

    @mock.patch("core.management.commands.update_feeds.Feed")
    @mock.patch("core.management.commands.update_feeds.update_multiple_feeds")
    @mock.patch("core.management.commands.update_feeds.logger")
    def test_update_feeds_for_frequency_all_frequencies(
        self, mock_logger, mock_update_multiple, mock_feed_model
    ):
        """Test update_feeds_for_frequency with all valid frequencies."""
        frequency_mapping = {
            "5 min": 5,
            "15 min": 15,
            "30 min": 30,
            "hourly": 60,
            "daily": 1440,
            "weekly": 10080,
        }
        
        mock_feed_model.objects.filter.return_value.iterator.return_value = iter([])
        
        for frequency, expected_value in frequency_mapping.items():
            with self.subTest(frequency=frequency):
                mock_feed_model.reset_mock()
                
                cmd.update_feeds_for_frequency(frequency)
                
                mock_feed_model.objects.filter.assert_called_once_with(
                    update_frequency=expected_value
                )


class AddArgumentsTests(SimpleTestCase):
    """Tests for `Command.add_arguments` method."""

    def setUp(self):
        self.command = cmd.Command()
        self.parser = ArgumentParser()

    def test_add_arguments_adds_frequency_parameter(self):
        """Test that add_arguments correctly adds the --frequency parameter."""
        self.command.add_arguments(self.parser)
        
        # Parse arguments to verify the parameter was added
        parsed_args = self.parser.parse_args(['--frequency', '5 min'])
        self.assertEqual(parsed_args.frequency, '5 min')

    def test_add_arguments_frequency_parameter_properties(self):
        """Test that the --frequency parameter has correct properties."""
        self.command.add_arguments(self.parser)
        
        # Find the frequency action in the parser
        frequency_action = None
        for action in self.parser._actions:
            if '--frequency' in action.option_strings:
                frequency_action = action
                break
        
        self.assertIsNotNone(frequency_action, "Frequency parameter should be added")
        self.assertEqual(frequency_action.type, str, "Frequency parameter should be str type")
        self.assertEqual(frequency_action.nargs, '?', "Frequency parameter should have nargs='?'")
        self.assertIn("Specify update frequency", frequency_action.help)

    def test_add_arguments_frequency_parameter_optional(self):
        """Test that --frequency parameter is optional."""
        self.command.add_arguments(self.parser)
        
        # Should be able to parse without --frequency
        parsed_args = self.parser.parse_args([])
        self.assertIsNone(parsed_args.frequency)

    def test_add_arguments_frequency_parameter_valid_values(self):
        """Test that --frequency parameter accepts valid frequency values."""
        self.command.add_arguments(self.parser)
        
        valid_frequencies = [
            "5 min", "15 min", "30 min", "hourly", "daily", "weekly"
        ]
        
        for frequency in valid_frequencies:
            with self.subTest(frequency=frequency):
                parsed_args = self.parser.parse_args(['--frequency', frequency])
                self.assertEqual(parsed_args.frequency, frequency)

    def test_add_arguments_frequency_without_value(self):
        """Test that --frequency can be specified without a value (nargs='?')."""
        self.command.add_arguments(self.parser)
        
        # With nargs='?', --frequency without value should set it to None
        parsed_args = self.parser.parse_args(['--frequency'])
        self.assertIsNone(parsed_args.frequency)


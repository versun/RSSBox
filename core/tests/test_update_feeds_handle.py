from unittest.mock import patch, Mock, call, mock_open
from argparse import ArgumentParser

from django.test import SimpleTestCase

from core.management.commands import update_feeds as cmd


class UpdateFeedsHandleTests(SimpleTestCase):
    """Tests for `Command.handle` function covering edge cases."""

    def setUp(self):
        """Set up test data."""
        self.command = cmd.Command()
        self.valid_frequencies = [
            "5 min",
            "15 min",
            "30 min",
            "hourly",
            "daily",
            "weekly",
        ]
        self.frequency_to_lock = {
            "5 min": "/tmp/update_feeds_5_min.lock",
            "15 min": "/tmp/update_feeds_15_min.lock",
            "30 min": "/tmp/update_feeds_30_min.lock",
            "hourly": "/tmp/update_feeds_hourly.lock",
            "daily": "/tmp/update_feeds_daily.lock",
            "weekly": "/tmp/update_feeds_weekly.lock",
        }

    def test_handle_invalid_frequency(self):
        """Invalid frequency string should raise SystemExit(1)."""
        with patch.object(self.command, "stdout"), patch.object(self.command, "stderr"):
            with self.assertRaises(SystemExit) as ctx:
                self.command.handle(frequency="2 hours")
            self.assertEqual(ctx.exception.code, 1)

    def test_handle_no_frequency_provided(self):
        """When no frequency is provided, should exit with code 1."""
        with patch.object(self.command, "stdout"), patch.object(self.command, "stderr"):
            with self.assertRaises(SystemExit) as ctx:
                self.command.handle(frequency=None)
            self.assertEqual(ctx.exception.code, 1)

    @patch("core.management.commands.update_feeds.os.remove")
    @patch("core.management.commands.update_feeds.open", new_callable=mock_open)
    @patch("core.management.commands.update_feeds.os.path.exists", return_value=True)
    def test_handle_lock_file_exists(self, mock_exists, mock_open_file, mock_remove):
        """When lock file present, command should exit with code 0 and not proceed."""
        with patch.object(self.command, "stdout"), patch.object(self.command, "stderr"):
            with self.assertRaises(SystemExit) as ctx:
                self.command.handle(frequency="5 min")
            self.assertEqual(ctx.exception.code, 0)
        mock_open_file.assert_not_called()
        mock_remove.assert_not_called()

    @patch("core.management.commands.update_feeds.os.remove")
    @patch("core.management.commands.update_feeds.open", new_callable=mock_open)
    @patch(
        "core.management.commands.update_feeds.os.path.exists",
        side_effect=[False, True],
    )
    @patch("core.management.commands.update_feeds.update_feeds_for_frequency")
    def test_handle_happy_path(
        self, mock_update, mock_exists, mock_open_file, mock_remove
    ):
        """Valid frequency without lock proceeds and cleans up lock file."""
        # Mock stdout and stderr for the command instance
        with patch.object(self.command, "stdout"), patch.object(self.command, "stderr"):
            self.command.handle(frequency="5 min")

        mock_open_file.assert_called()
        mock_update.assert_called_once_with(simple_update_frequency="5 min")
        mock_remove.assert_called()

    def test_handle_all_valid_frequencies(self):
        """Test that all valid frequencies are accepted."""
        for frequency in self.valid_frequencies:
            with self.subTest(frequency=frequency):
                with (
                    patch(
                        "core.management.commands.update_feeds.os.path.exists",
                        return_value=False,
                    ),
                    patch(
                        "core.management.commands.update_feeds.open",
                        new_callable=mock_open,
                    ),
                    patch("core.management.commands.update_feeds.os.remove"),
                    patch(
                        "core.management.commands.update_feeds.update_feeds_for_frequency"
                    ) as mock_update,
                ):
                    # Mock stdout and stderr for the command instance
                    with (
                        patch.object(self.command, "stdout"),
                        patch.object(self.command, "stderr"),
                    ):
                        self.command.handle(frequency=frequency)
                    mock_update.assert_called_once_with(
                        simple_update_frequency=frequency
                    )

    @patch("core.management.commands.update_feeds.os.remove")
    @patch("core.management.commands.update_feeds.open", new_callable=mock_open)
    @patch(
        "core.management.commands.update_feeds.os.path.exists",
        side_effect=[False, True],
    )
    @patch("core.management.commands.update_feeds.update_feeds_for_frequency")
    def test_handle_exception_during_update(
        self, mock_update, mock_exists, mock_open_file, mock_remove
    ):
        """Test exception handling during update_feeds_for_frequency."""
        mock_update.side_effect = Exception("Update failed")

        # Mock stdout and stderr for the command instance
        with patch.object(self.command, "stdout"), patch.object(self.command, "stderr"):
            with self.assertRaises(SystemExit) as ctx:
                self.command.handle(frequency="5 min")

        self.assertEqual(ctx.exception.code, 1)
        mock_remove.assert_called()

    @patch("core.management.commands.update_feeds.os.remove")
    @patch("core.management.commands.update_feeds.open", new_callable=mock_open)
    @patch(
        "core.management.commands.update_feeds.os.path.exists",
        side_effect=[False, True],
    )
    @patch("core.management.commands.update_feeds.update_feeds_for_frequency")
    @patch("core.management.commands.update_feeds.logger")
    def test_handle_exception_logging(
        self, mock_logger, mock_update, mock_exists, mock_open_file, mock_remove
    ):
        """Test that exceptions are properly logged."""
        mock_update.side_effect = Exception("Update failed")

        # Mock stdout and stderr for the command instance
        with patch.object(self.command, "stdout"), patch.object(self.command, "stderr"):
            with self.assertRaises(SystemExit):
                self.command.handle(frequency="5 min")

        mock_logger.exception.assert_called_once_with(
            "Command update_feeds_for_frequency failed: Update failed"
        )

    @patch("core.management.commands.update_feeds.os.remove")
    @patch("core.management.commands.update_feeds.open", new_callable=mock_open)
    @patch("core.management.commands.update_feeds.os.path.exists", return_value=False)
    @patch("core.management.commands.update_feeds.update_feeds_for_frequency")
    def test_handle_lock_file_creation_and_content(
        self, mock_update, mock_exists, mock_open_file, mock_remove
    ):
        """Test that lock file is created with correct content (PID)."""
        # Mock stdout and stderr for the command instance
        with patch.object(self.command, "stdout"), patch.object(self.command, "stderr"):
            with patch(
                "core.management.commands.update_feeds.os.getpid", return_value=12345
            ):
                self.command.handle(frequency="hourly")

        expected_lock_path = "/tmp/update_feeds_hourly.lock"
        mock_open_file.assert_called_with(expected_lock_path, "w")

        handle = mock_open_file.return_value.__enter__.return_value
        handle.write.assert_called_once_with("12345")

    @patch("core.management.commands.update_feeds.os.remove")
    @patch("core.management.commands.update_feeds.open", new_callable=mock_open)
    @patch(
        "core.management.commands.update_feeds.os.path.exists",
        side_effect=[False, False],
    )
    @patch("core.management.commands.update_feeds.update_feeds_for_frequency")
    def test_handle_lock_file_cleanup_when_not_exists(
        self, mock_update, mock_exists, mock_open_file, mock_remove
    ):
        """Test that remove is not called if lock file doesn't exist in finally block."""
        # Mock stdout and stderr for the command instance
        with patch.object(self.command, "stdout"), patch.object(self.command, "stderr"):
            self.command.handle(frequency="5 min")
        mock_remove.assert_not_called()

    def test_handle_frequency_to_lock_file_mapping(self):
        """Test that frequency strings are correctly converted to lock file names."""
        for frequency, expected_lock_path in self.frequency_to_lock.items():
            with self.subTest(frequency=frequency):
                with patch(
                    "core.management.commands.update_feeds.os.path.exists",
                    return_value=True,
                ) as mock_exists:
                    with (
                        patch.object(self.command, "stdout"),
                        patch.object(self.command, "stderr"),
                    ):
                        with self.assertRaises(SystemExit):
                            self.command.handle(frequency=frequency)
                    mock_exists.assert_called_with(expected_lock_path)


class UpdateSingleFeedTests(SimpleTestCase):
    """Tests for update_single_feed function."""

    def setUp(self):
        """Set up test data."""
        self.mock_feed = Mock()
        self.mock_feed.name = "Test Feed"
        self.mock_feed.translate_title = False
        self.mock_feed.translate_content = False
        self.mock_feed.summary = False

    def _patch_common_mocks(self):
        """Helper method to patch common mocks."""
        patches = [
            patch("core.management.commands.update_feeds.close_old_connections"),
            patch("core.management.commands.update_feeds.handle_single_feed_fetch"),
            patch("core.management.commands.update_feeds.logger"),
        ]
        return [p.start() for p in patches]

    @patch("core.management.commands.update_feeds.close_old_connections")
    @patch("core.management.commands.update_feeds.handle_single_feed_fetch")
    @patch("core.management.commands.update_feeds.handle_feeds_translation")
    @patch("core.management.commands.update_feeds.handle_feeds_summary")
    @patch("core.management.commands.update_feeds.logger")
    def test_update_single_feed_success_with_all_options(
        self, mock_logger, mock_summary, mock_translation, mock_fetch, mock_close_conn
    ):
        """Test successful feed update with all translation and summary options enabled."""
        self.mock_feed.translate_title = True
        self.mock_feed.translate_content = True
        self.mock_feed.summary = True

        result = cmd.update_single_feed(self.mock_feed)

        self.assertTrue(result)
        mock_close_conn.assert_called()
        mock_fetch.assert_called_once_with(self.mock_feed)

        expected_calls = [
            call([self.mock_feed], target_field="title"),
            call([self.mock_feed], target_field="content"),
        ]
        mock_translation.assert_has_calls(expected_calls)

        mock_summary.assert_called_once_with([self.mock_feed])
        mock_logger.info.assert_any_call(f"Starting feed update: {self.mock_feed.name}")
        mock_logger.info.assert_any_call(
            f"Completed feed update: {self.mock_feed.name}"
        )

    @patch("core.management.commands.update_feeds.close_old_connections")
    @patch("core.management.commands.update_feeds.handle_single_feed_fetch")
    @patch("core.management.commands.update_feeds.logger")
    def test_update_single_feed_success_minimal_options(
        self, mock_logger, mock_fetch, mock_close_conn
    ):
        """Test successful feed update with minimal options (no translation/summary)."""
        result = cmd.update_single_feed(self.mock_feed)

        self.assertTrue(result)
        mock_fetch.assert_called_once_with(self.mock_feed)
        mock_logger.info.assert_any_call(f"Starting feed update: {self.mock_feed.name}")
        mock_logger.info.assert_any_call(
            f"Completed feed update: {self.mock_feed.name}"
        )

    def test_update_single_feed_exceptions(self):
        """Test exception handling in update_single_feed."""
        mock_close_conn, mock_fetch, mock_logger = self._patch_common_mocks()

        # Test Feed.DoesNotExist exception
        from core.models import Feed

        mock_fetch.side_effect = Feed.DoesNotExist("Feed not found")
        result = cmd.update_single_feed(self.mock_feed)
        self.assertFalse(result)
        mock_logger.error.assert_called_once_with(
            f"Feed not found: ID {self.mock_feed.name}"
        )

        # Reset mock and test general exception
        mock_fetch.reset_mock()
        mock_logger.reset_mock()
        mock_fetch.side_effect = Exception("Fetch failed")

        result = cmd.update_single_feed(self.mock_feed)
        self.assertFalse(result)
        mock_logger.exception.assert_called_once_with(
            f"Error updating feed ID {self.mock_feed.name}: Fetch failed"
        )

        mock_close_conn.assert_called()


class UpdateMultipleFeedsTests(SimpleTestCase):
    """Tests for update_multiple_feeds function."""

    def setUp(self):
        """Set up test data."""
        self.mock_feed1 = Mock()
        self.mock_feed1.name = "Feed 1"
        self.mock_feed1.slug = "feed-1"
        self.mock_feed1.tags.values_list.return_value = [1, 2]

        self.mock_feed2 = Mock()
        self.mock_feed2.name = "Feed 2"
        self.mock_feed2.slug = "feed-2"
        self.mock_feed2.tags.values_list.return_value = [2, 3]

    @patch("core.management.commands.update_feeds.logger")
    def test_update_multiple_feeds_empty_list(self, mock_logger):
        """Test update_multiple_feeds with empty feed list."""
        cmd.update_multiple_feeds([])
        mock_logger.info.assert_called_once_with("No feeds to update.")

    def _create_mock_futures(self, success_count=2):
        """Helper method to create mock futures."""
        futures = []
        for i in range(success_count):
            mock_future = Mock()
            mock_future.result.return_value = True
            futures.append(mock_future)
        return futures

    def _create_mock_tags(self):
        """Helper method to create mock tags."""
        mock_tag1 = Mock(slug="tag-1")
        mock_tag2 = Mock(slug="tag-2")
        return [mock_tag1, mock_tag2]

    @patch("core.management.commands.update_feeds.task_manager")
    @patch("core.management.commands.update_feeds.wait")
    @patch("core.management.commands.update_feeds.cache_rss")
    @patch("core.management.commands.update_feeds.cache_tag")
    @patch("core.management.commands.update_feeds.Tag")
    @patch("core.management.commands.update_feeds.logger")
    def test_update_multiple_feeds_success(
        self,
        mock_logger,
        mock_tag_model,
        mock_cache_tag,
        mock_cache_rss,
        mock_wait,
        mock_task_manager,
    ):
        """Test successful update of multiple feeds with caching."""
        feeds = [self.mock_feed1, self.mock_feed2]
        mock_futures = self._create_mock_futures(2)
        mock_task_manager.submit_task.side_effect = mock_futures
        mock_wait.return_value = (mock_futures, [])
        mock_tag_model.objects.filter.return_value = self._create_mock_tags()

        cmd.update_multiple_feeds(feeds)

        # Verify task submission
        self.assertEqual(mock_task_manager.submit_task.call_count, 2)
        mock_task_manager.submit_task.assert_any_call(
            "update_feed_Feed 1", cmd.update_single_feed, self.mock_feed1
        )
        mock_task_manager.submit_task.assert_any_call(
            "update_feed_Feed 2", cmd.update_single_feed, self.mock_feed2
        )

        # Verify wait call
        mock_wait.assert_called_once_with(mock_futures, timeout=1800)

        # Verify RSS caching for each feed
        expected_rss_calls = []
        for feed in feeds:
            for feed_type in ["o", "t"]:
                for format_type in ["xml", "json"]:
                    expected_rss_calls.append(
                        call(feed.slug, feed_type=feed_type, format=format_type)
                    )

        mock_cache_rss.assert_has_calls(expected_rss_calls, any_order=True)

        # Verify tag caching
        expected_tag_calls = []
        for tag in self._create_mock_tags():
            expected_tag_calls.extend(
                [
                    call(tag.slug, feed_type="o", format="xml"),
                    call(tag.slug, feed_type="t", format="xml"),
                    call(tag.slug, feed_type="t", format="json"),
                ]
            )

        mock_cache_tag.assert_has_calls(expected_tag_calls, any_order=True)

    @patch("core.management.commands.update_feeds.task_manager")
    @patch("core.management.commands.update_feeds.wait")
    @patch("core.management.commands.update_feeds.logger")
    def test_update_multiple_feeds_timeout(
        self, mock_logger, mock_wait, mock_task_manager
    ):
        """Test update_multiple_feeds when some tasks timeout."""
        mock_future1, mock_future2 = self._create_mock_futures(2)
        mock_task_manager.submit_task.return_value = mock_future1

        # Mock timeout scenario
        mock_wait.return_value = ([mock_future1], [mock_future2])

        cmd.update_multiple_feeds([self.mock_feed1])

        mock_logger.warning.assert_called_once_with(
            "Feed update task timed out. 1 tasks did not complete."
        )

    @patch("core.management.commands.update_feeds.task_manager")
    @patch("core.management.commands.update_feeds.wait")
    @patch("core.management.commands.update_feeds.cache_rss")
    @patch("core.management.commands.update_feeds.logger")
    def test_update_multiple_feeds_task_exception(
        self, mock_logger, mock_cache_rss, mock_wait, mock_task_manager
    ):
        """Test update_multiple_feeds when a task raises an exception."""
        mock_future = Mock()
        mock_future.result.side_effect = Exception("Task failed")
        mock_task_manager.submit_task.return_value = mock_future
        mock_wait.return_value = ([mock_future], [])

        cmd.update_multiple_feeds([self.mock_feed1])

        mock_logger.warning.assert_called_once_with(
            "A feed update task resulted in an exception: Task failed"
        )

    @patch("core.management.commands.update_feeds.task_manager")
    @patch("core.management.commands.update_feeds.wait")
    @patch("core.management.commands.update_feeds.cache_rss")
    @patch("core.management.commands.update_feeds.logger")
    def test_update_multiple_feeds_cache_exception(
        self, mock_logger, mock_cache_rss, mock_wait, mock_task_manager
    ):
        """Test update_multiple_feeds when RSS caching fails."""
        mock_future = Mock()
        mock_task_manager.submit_task.return_value = mock_future
        mock_wait.return_value = ([mock_future], [])

        # First cache call succeeds, second fails
        mock_cache_rss.side_effect = [None, Exception("Cache failed"), None, None]

        with patch(
            "core.management.commands.update_feeds.time.time", return_value=1234567890
        ):
            cmd.update_multiple_feeds([self.mock_feed1])

        mock_logger.error.assert_called_with(
            f"1234567890: Failed to cache RSS for {self.mock_feed1.slug}: Cache failed"
        )

    @patch("core.management.commands.update_feeds.task_manager")
    @patch("core.management.commands.update_feeds.logger")
    def test_update_multiple_feeds_general_exception(
        self, mock_logger, mock_task_manager
    ):
        """Test update_multiple_feeds when a general exception occurs."""
        mock_task_manager.submit_task.side_effect = Exception("General error")

        cmd.update_multiple_feeds([self.mock_feed1])

        mock_logger.exception.assert_called_once_with(
            "Command update_multiple_feeds failed: %s", "General error"
        )

    @patch("core.management.commands.update_feeds.task_manager")
    @patch("core.management.commands.update_feeds.wait")
    @patch("core.management.commands.update_feeds.cache_rss")
    @patch("core.management.commands.update_feeds.cache_tag")
    @patch("core.management.commands.update_feeds.Tag")
    @patch("core.management.commands.update_feeds.logger")
    def test_update_multiple_feeds_cache_tag_exception(
        self,
        mock_logger,
        mock_tag_model,
        mock_cache_tag,
        mock_cache_rss,
        mock_wait,
        mock_task_manager,
    ):
        """Test update_multiple_feeds when tag caching fails."""
        mock_future = Mock()
        mock_task_manager.submit_task.return_value = mock_future
        mock_wait.return_value = ([mock_future], [])

        # Mock tags
        mock_tag1 = Mock(slug="tech-news")
        mock_tag2 = Mock(slug="ai-updates")
        mock_tag_model.objects.filter.return_value = [mock_tag1, mock_tag2]

        # RSS caching succeeds, but tag caching fails for second tag
        mock_cache_tag.side_effect = [
            None,  # tag1: cache_tag(tech-news, feed_type="o", format="xml")
            None,  # tag1: cache_tag(tech-news, feed_type="t", format="xml")
            None,  # tag1: cache_tag(tech-news, feed_type="t", format="json")
            Exception(
                "Tag cache failed"
            ),  # tag2: cache_tag(ai-updates, feed_type="o", format="xml") fails
        ]

        cmd.update_multiple_feeds([self.mock_feed1])

        mock_logger.error.assert_called_with(
            f"Failed to cache tag {mock_tag2.slug}: Tag cache failed"
        )


class UpdateFeedsForFrequencyTests(SimpleTestCase):
    """Tests for update_feeds_for_frequency function."""

    def setUp(self):
        """Set up test data."""
        self.frequency_mapping = {
            "5 min": 5,
            "15 min": 15,
            "30 min": 30,
            "hourly": 60,
            "daily": 1440,
            "weekly": 10080,
        }

    @patch("core.management.commands.update_feeds.Feed")
    @patch("core.management.commands.update_feeds.update_multiple_feeds")
    @patch("core.management.commands.update_feeds.logger")
    def test_update_feeds_for_frequency_success(
        self, mock_logger, mock_update_multiple, mock_feed_model
    ):
        """Test successful update_feeds_for_frequency call."""
        mock_feed1, mock_feed2 = Mock(), Mock()
        mock_feeds_iterator = iter([mock_feed1, mock_feed2])

        mock_feed_model.objects.filter.return_value.iterator.return_value = (
            mock_feeds_iterator
        )

        with patch(
            "core.management.commands.update_feeds.current_time", "2024-01-01 12:00:00"
        ):
            cmd.update_feeds_for_frequency("hourly")

        mock_feed_model.objects.filter.assert_called_once_with(update_frequency=60)
        mock_logger.info.assert_called_once_with(
            "2024-01-01 12:00:00: Start update feeds for frequency: hourly, feeds count: 2"
        )
        mock_update_multiple.assert_called_once()
        args = mock_update_multiple.call_args[0][0]
        self.assertEqual(len(args), 2)

    @patch("core.management.commands.update_feeds.logger")
    def test_update_feeds_for_frequency_invalid_frequency(self, mock_logger):
        """Test update_feeds_for_frequency with invalid frequency."""
        cmd.update_feeds_for_frequency("invalid")
        mock_logger.error.assert_called_once_with("Invalid frequency: invalid")

    @patch("core.management.commands.update_feeds.Feed")
    @patch("core.management.commands.update_feeds.update_multiple_feeds")
    @patch("core.management.commands.update_feeds.logger")
    def test_update_feeds_for_frequency_exception(
        self, mock_logger, mock_update_multiple, mock_feed_model
    ):
        """Test update_feeds_for_frequency when an exception occurs."""
        mock_feed_model.objects.filter.side_effect = Exception("Database error")

        with patch(
            "core.management.commands.update_feeds.current_time", "2024-01-01 12:00:00"
        ):
            cmd.update_feeds_for_frequency("daily")

        mock_logger.exception.assert_called_once_with(
            "2024-01-01 12:00:00: Command update_feeds_for_frequency daily: Database error"
        )

    @patch("core.management.commands.update_feeds.Feed")
    @patch("core.management.commands.update_feeds.update_multiple_feeds")
    @patch("core.management.commands.update_feeds.logger")
    def test_update_feeds_for_frequency_all_frequencies(
        self, mock_logger, mock_update_multiple, mock_feed_model
    ):
        """Test update_feeds_for_frequency with all valid frequencies."""
        mock_feed_model.objects.filter.return_value.iterator.return_value = iter([])

        for frequency, expected_value in self.frequency_mapping.items():
            with self.subTest(frequency=frequency):
                mock_feed_model.reset_mock()
                cmd.update_feeds_for_frequency(frequency)
                mock_feed_model.objects.filter.assert_called_once_with(
                    update_frequency=expected_value
                )


class AddArgumentsTests(SimpleTestCase):
    """Tests for `Command.add_arguments` method."""

    def setUp(self):
        """Set up test data."""
        self.command = cmd.Command()
        self.parser = ArgumentParser()
        self.valid_frequencies = [
            "5 min",
            "15 min",
            "30 min",
            "hourly",
            "daily",
            "weekly",
        ]

    def test_add_arguments_adds_frequency_parameter(self):
        """Test that add_arguments correctly adds the --frequency parameter."""
        self.command.add_arguments(self.parser)
        parsed_args = self.parser.parse_args(["--frequency", "5 min"])
        self.assertEqual(parsed_args.frequency, "5 min")

    def test_add_arguments_frequency_parameter_properties(self):
        """Test that the --frequency parameter has correct properties."""
        self.command.add_arguments(self.parser)

        frequency_action = None
        for action in self.parser._actions:
            if "--frequency" in action.option_strings:
                frequency_action = action
                break

        self.assertIsNotNone(frequency_action, "Frequency parameter should be added")
        self.assertEqual(
            frequency_action.type, str, "Frequency parameter should be str type"
        )
        self.assertEqual(
            frequency_action.nargs, "?", "Frequency parameter should have nargs='?'"
        )
        self.assertIn("Specify update frequency", frequency_action.help)

    def test_add_arguments_frequency_parameter_optional(self):
        """Test that --frequency parameter is optional."""
        self.command.add_arguments(self.parser)
        parsed_args = self.parser.parse_args([])
        self.assertIsNone(parsed_args.frequency)

    def test_add_arguments_frequency_parameter_valid_values(self):
        """Test that --frequency parameter accepts valid frequency values."""
        self.command.add_arguments(self.parser)

        for frequency in self.valid_frequencies:
            with self.subTest(frequency=frequency):
                parsed_args = self.parser.parse_args(["--frequency", frequency])
                self.assertEqual(parsed_args.frequency, frequency)

    def test_add_arguments_frequency_without_value(self):
        """Test that --frequency can be specified without a value (nargs='?')."""
        self.command.add_arguments(self.parser)
        parsed_args = self.parser.parse_args(["--frequency"])
        self.assertIsNone(parsed_args.frequency)

from django.test import TestCase, override_settings
from unittest.mock import patch, Mock, mock_open, call
import fcntl
import os
import tempfile
import shutil
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import close_old_connections
from io import StringIO

from core.management.commands.cleanup_entries import (
    Command,
    cleanup_feed_entries,
    cleanup_all_feeds,
)
from core.models import Feed, Entry


class CleanupEntriesCommandTests(TestCase):
    """Test cases for cleanup_entries management command."""

    def setUp(self):
        """Set up test data."""
        self.feed = Feed.objects.create(
            name="Test Feed", feed_url="http://example.com/feed", max_posts=5
        )

        # Create test entries
        for i in range(10):
            Entry.objects.create(
                feed=self.feed,
                link=f"http://example.com/entry{i}",
                original_title=f"Entry {i}",
                original_content=f"Content {i}",
            )

    def test_cleanup_feed_entries_within_limit(self):
        """Test cleanup when feed has entries within max_posts limit."""
        # Set max_posts to 15 (more than current entries)
        self.feed.max_posts = 15
        self.feed.save()

        initial_count = self.feed.entries.count()
        cleanup_feed_entries(self.feed)

        # Should not delete any entries
        self.assertEqual(self.feed.entries.count(), initial_count)

    def test_cleanup_feed_entries_exceeds_limit(self):
        """Test cleanup when feed exceeds max_posts limit."""
        # Set max_posts to 3 (less than current entries)
        self.feed.max_posts = 3
        self.feed.save()

        initial_count = self.feed.entries.count()
        cleanup_feed_entries(self.feed)

        # Should keep only 3 latest entries
        self.assertEqual(self.feed.entries.count(), 3)

        # Should keep entries with highest IDs (latest)
        kept_entries = list(self.feed.entries.order_by("-id")[:3])
        expected_ids = [entry.id for entry in kept_entries]

        for entry in self.feed.entries.all():
            self.assertIn(entry.id, expected_ids)

    def test_cleanup_all_feeds_success(self):
        """Test cleanup_all_feeds function with multiple feeds."""
        # Create another feed
        feed2 = Feed.objects.create(
            name="Test Feed 2", feed_url="http://example.com/feed2", max_posts=2
        )

        # Create entries for second feed
        for i in range(5):
            Entry.objects.create(
                feed=feed2,
                link=f"http://example.com/feed2/entry{i}",
                original_title=f"Feed2 Entry {i}",
                original_content=f"Feed2 Content {i}",
            )

        # Run cleanup for all feeds
        cleanup_all_feeds()

        # Check first feed still has 5 entries (within limit)
        self.assertEqual(self.feed.entries.count(), 5)

        # Check second feed has 2 entries (within limit)
        self.assertEqual(feed2.entries.count(), 2)

    def test_cleanup_all_feeds_empty_feeds(self):
        """Test cleanup_all_feeds function with no feeds."""
        # Delete all feeds
        Feed.objects.all().delete()

        # Should not raise any exception
        cleanup_all_feeds()

        # Verify no feeds exist
        self.assertEqual(Feed.objects.count(), 0)

    def test_cleanup_feed_entries_exception_handling(self):
        """Test cleanup_feed_entries exception handling."""
        # Mock close_old_connections to raise exception only in try block
        with patch(
            "core.management.commands.cleanup_entries.close_old_connections"
        ) as mock_close:
            # First call raises exception, second call (in finally) works normally
            mock_close.side_effect = [Exception("Connection error"), None]

            # Should not raise exception, should handle it gracefully
            cleanup_feed_entries(self.feed)

            # Verify close_old_connections was called twice
            self.assertEqual(mock_close.call_count, 2)  # Once in try, once in finally

    def test_cleanup_feed_entries_database_exception(self):
        """Test cleanup_feed_entries with database exception."""
        # Mock feed.entries.count to raise exception
        with patch.object(self.feed.entries, "count") as mock_count:
            mock_count.side_effect = Exception("Database error")

            # Should not raise exception, should handle it gracefully
            cleanup_feed_entries(self.feed)

            # Verify close_old_connections was called in finally block
            with patch(
                "core.management.commands.cleanup_entries.close_old_connections"
            ) as mock_close:
                cleanup_feed_entries(self.feed)
                self.assertEqual(mock_close.call_count, 2)

    def test_cleanup_all_feeds_exception_handling(self):
        """Test cleanup_all_feeds exception handling."""
        # Mock Feed.objects.all to raise exception
        with patch("core.management.commands.cleanup_entries.Feed") as mock_feed_model:
            mock_feed_model.objects.all.side_effect = Exception("Database error")

            # Should raise exception
            with self.assertRaises(Exception) as cm:
                cleanup_all_feeds()

            self.assertEqual(str(cm.exception), "Database error")

    def test_cleanup_all_feeds_batch_processing(self):
        """Test cleanup_all_feeds batch processing with more than 10 feeds."""
        # Create more than 10 feeds to test batch processing
        feeds = []
        for i in range(15):
            feed = Feed.objects.create(
                name=f"Test Feed {i}",
                feed_url=f"http://example.com/feed{i}",
                max_posts=2,
            )

            # Create entries for each feed
            for j in range(5):
                Entry.objects.create(
                    feed=feed,
                    link=f"http://example.com/feed{i}/entry{j}",
                    original_title=f"Feed{i} Entry {j}",
                    original_content=f"Feed{i} Content {j}",
                )
            feeds.append(feed)

        # Mock logger to capture log messages
        with patch("core.management.commands.cleanup_entries.logger") as mock_logger:
            cleanup_all_feeds()

            # Verify batch processing log messages
            # Check that the expected log messages were called with any arguments
            # The actual format includes timestamp, so we need to check more flexibly
            processing_calls = [
                call
                for call in mock_logger.info.call_args_list
                if "Processing feed 10/" in str(call)
            ]
            completed_calls = [
                call
                for call in mock_logger.info.call_args_list
                if "Completed cleanup for" in str(call)
            ]

            self.assertTrue(
                len(processing_calls) > 0, "Processing feed log message not found"
            )
            self.assertTrue(
                len(completed_calls) > 0, "Completed cleanup log message not found"
            )

    def test_cleanup_feed_entries_exact_limit(self):
        """Test cleanup when feed has exactly max_posts entries."""
        # Set max_posts to exactly match current entries
        self.feed.max_posts = 10
        self.feed.save()

        initial_count = self.feed.entries.count()
        cleanup_feed_entries(self.feed)

        # Should not delete any entries
        self.assertEqual(self.feed.entries.count(), initial_count)

    def test_cleanup_feed_entries_zero_max_posts(self):
        """Test cleanup when feed has max_posts = 0."""
        # Set max_posts to 0
        self.feed.max_posts = 0
        self.feed.save()

        cleanup_feed_entries(self.feed)

        # Should delete all entries
        self.assertEqual(self.feed.entries.count(), 0)

    def test_cleanup_feed_entries_negative_max_posts(self):
        """Test cleanup when feed has negative max_posts."""
        # Set max_posts to negative value
        self.feed.max_posts = -1
        self.feed.save()

        cleanup_feed_entries(self.feed)

        # Should not delete any entries (negative values are treated as 0)
        self.assertEqual(self.feed.entries.count(), 10)


class CleanupEntriesCommandClassTests(TestCase):
    """Test cases for the Command class."""

    def setUp(self):
        """Set up test data."""
        self.command = Command()
        self.feed = Feed.objects.create(
            name="Test Feed", feed_url="http://example.com/feed", max_posts=3
        )

        # Create test entries
        for i in range(5):
            Entry.objects.create(
                feed=self.feed,
                link=f"http://example.com/entry{i}",
                original_title=f"Entry {i}",
                original_content=f"Content {i}",
            )

    @patch("core.management.commands.cleanup_entries.fcntl")
    @patch("core.management.commands.cleanup_entries.open", new_callable=mock_open)
    @patch("core.management.commands.cleanup_entries.cleanup_all_feeds")
    def test_command_success(self, mock_cleanup, mock_file, mock_fcntl):
        """Test successful command execution."""
        # Capture stdout
        from io import StringIO

        out = StringIO()
        self.command.stdout = out

        # Execute command
        self.command.handle()

        # Verify cleanup was called
        mock_cleanup.assert_called_once()

        # Verify the flock was acquired
        mock_fcntl.flock.assert_called_once()

        # Verify success message
        self.assertIn("Successfully cleaned up all feeds", out.getvalue())

    @patch("core.management.commands.cleanup_entries.fcntl")
    @patch("core.management.commands.cleanup_entries.open", new_callable=mock_open)
    @patch("core.management.commands.cleanup_entries.cleanup_all_feeds")
    def test_command_already_running(self, mock_cleanup, mock_file, mock_fcntl):
        """Test command when cleanup is already running (flock held elsewhere)."""
        mock_fcntl.flock.side_effect = BlockingIOError()

        # Capture stdout
        from io import StringIO

        out = StringIO()
        self.command.stdout = out

        # Execute command
        with self.assertRaises(SystemExit) as cm:
            self.command.handle()

        # Should exit with code 0
        self.assertEqual(cm.exception.code, 0)

        # Verify warning message
        self.assertIn("Cleanup process is already running", out.getvalue())

        # Verify cleanup did not run
        mock_cleanup.assert_not_called()

    @patch("core.management.commands.cleanup_entries.fcntl")
    @patch("core.management.commands.cleanup_entries.open", new_callable=mock_open)
    @patch("core.management.commands.cleanup_entries.cleanup_all_feeds")
    @patch("core.management.commands.cleanup_entries.logger")
    def test_command_exception_handling(
        self, mock_logger, mock_cleanup, mock_file, mock_fcntl
    ):
        """Test command exception handling."""
        # Mock cleanup function to raise exception
        mock_cleanup.side_effect = Exception("Test error")

        # Capture stderr
        from io import StringIO

        err = StringIO()
        self.command.stderr = err

        # Execute command
        with self.assertRaises(SystemExit) as cm:
            self.command.handle()

        # Should exit with code 1
        self.assertEqual(cm.exception.code, 1)

        # Verify error message
        self.assertIn("Test error", err.getvalue())


class CleanupEntriesLockRegressionTests(TestCase):
    """Regression tests for issue #161 using a real lock file on disk."""

    lock_path = "/tmp/cleanup_entries.lock"

    def tearDown(self):
        if os.path.exists(self.lock_path):
            os.remove(self.lock_path)

    @patch("core.management.commands.cleanup_entries.cleanup_all_feeds")
    def test_stale_lock_file_does_not_block_cleanup(self, mock_cleanup):
        """A leftover lock file from a dead process must not block the next run."""
        # Simulate the stale lock left behind by a killed process (issue #161):
        # the file exists and contains a dead PID, but nobody holds the flock.
        with open(self.lock_path, "w") as f:
            f.write("999999")

        command = Command()
        with patch.object(command, "stdout"), patch.object(command, "stderr"):
            command.handle()

        mock_cleanup.assert_called_once()

    @patch("core.management.commands.cleanup_entries.cleanup_all_feeds")
    def test_lock_held_by_live_process_blocks_cleanup(self, mock_cleanup):
        """While a live process holds the flock, the command exits without cleaning."""
        with open(self.lock_path, "w") as holder:
            fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

            command = Command()
            with patch.object(command, "stdout"), patch.object(command, "stderr"):
                with self.assertRaises(SystemExit) as ctx:
                    command.handle()
            self.assertEqual(ctx.exception.code, 0)

        mock_cleanup.assert_not_called()

    @patch("core.management.commands.cleanup_entries.cleanup_all_feeds")
    def test_lock_released_after_failed_run(self, mock_cleanup):
        """A failed run releases the lock, so the next run still proceeds."""
        command = Command()
        with patch.object(command, "stdout"), patch.object(command, "stderr"):
            mock_cleanup.side_effect = Exception("boom")
            with self.assertRaises(SystemExit):
                command.handle()

            mock_cleanup.side_effect = None
            command.handle()

        self.assertEqual(mock_cleanup.call_count, 2)

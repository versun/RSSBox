from unittest import mock

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
    @mock.patch("core.management.commands.update_feeds.open", new_callable=mock.mock_open)
    @mock.patch("core.management.commands.update_feeds.os.path.exists", return_value=True)
    def test_handle_lock_file_exists(self, mock_exists, mock_open_file, mock_remove):
        """When lock file present, command should exit with code 0 and not proceed."""
        with self.assertRaises(SystemExit) as ctx:
            self.command.handle(frequency="5 min")
        self.assertEqual(ctx.exception.code, 0)
        # Should not attempt to open lock file or remove it
        mock_open_file.assert_not_called()
        mock_remove.assert_not_called()

    @mock.patch("core.management.commands.update_feeds.os.remove")
    @mock.patch("core.management.commands.update_feeds.open", new_callable=mock.mock_open)
    @mock.patch("core.management.commands.update_feeds.os.path.exists", side_effect=[False, True])
    @mock.patch("core.management.commands.update_feeds.update_feeds_for_frequency")
    def test_handle_happy_path(self, mock_update, mock_exists, mock_open_file, mock_remove):
        """Valid frequency without lock proceeds and cleans up lock file."""
        self.command.handle(frequency="5 min")

        # Lock file written
        mock_open_file.assert_called()
        # Helper called
        mock_update.assert_called_once_with(simple_update_frequency="5 min")
        # Lock file removed in finally block
        mock_remove.assert_called()

from django.test import SimpleTestCase
from unittest import mock

from core.management.commands import update_feeds as cmd


class UpdateFeedsCommandTests(SimpleTestCase):
    """Unit tests for helper functions inside update_feeds.py (excluding threading)."""

    @mock.patch("core.management.commands.update_feeds.update_multiple_feeds")
    @mock.patch("core.management.commands.update_feeds.Feed")
    def test_update_feeds_for_frequency_success(self, mock_feed_model, mock_update_multi):
        """When given a valid frequency, function should call update_multiple_feeds with list."""
        # Mock queryset filter/iterator to yield empty list
        mock_feed_model.objects.filter.return_value.iterator.return_value = []

        cmd.update_feeds_for_frequency("5 min")

        mock_feed_model.objects.filter.assert_called_once_with(update_frequency=5)
        mock_update_multi.assert_called_once_with([])

    def test_update_feeds_for_frequency_invalid(self):
        """Invalid frequency should not raise but log error."""
        # Function should handle invalid key internally without exception
        cmd.update_feeds_for_frequency("2 hours")  # Not in map -> KeyError handled

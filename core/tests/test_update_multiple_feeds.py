from django.test import TestCase
from unittest.mock import patch, Mock
from concurrent.futures import Future

from core.management.commands import update_feeds as cmd
from core.models import Feed, Tag


class UpdateMultipleFeedsTests(TestCase):
    """Unit tests for update_multiple_feeds helper."""

    def setUp(self):
        """Set up test data."""
        self.tag = Tag.objects.create(name="Tech")
        self.feed1 = Feed.objects.create(feed_url="https://a.com/rss.xml", name="A")
        self.feed1.tags.add(self.tag)
        self.feed2 = Feed.objects.create(feed_url="https://b.com/rss.xml", name="B")
        # no tag for feed2

    def _create_mock_future(self, result=True):
        """Helper method to create mock future."""
        future = Mock(spec=Future)
        future.result.return_value = result
        return future

    @patch("core.management.commands.update_feeds.cache_tag")
    @patch("core.management.commands.update_feeds.cache_rss")
    @patch("core.management.commands.update_feeds.wait")
    @patch("core.management.commands.update_feeds.task_manager")
    def test_update_multiple_feeds_basic(
        self, mock_tm, mock_wait, mock_cache_rss, mock_cache_tag
    ):
        """Ensure submit_task called and caching functions called for each feed."""
        feeds = [self.feed1, self.feed2]
        mock_futures = [self._create_mock_future() for _ in feeds]

        # Mock submit_task to return completed futures
        mock_tm.submit_task.side_effect = mock_futures
        # Mock wait returns all in done
        mock_wait.return_value = (set(mock_futures), set())

        cmd.update_multiple_feeds(feeds)

        # Two tasks submitted
        self.assertEqual(mock_tm.submit_task.call_count, 2)
        # cache_rss called 4 times per feed (o/xml, o/json, t/xml, t/json)
        self.assertEqual(mock_cache_rss.call_count, 4 * len(feeds))
        # cache_tag should be called three times (o/xml, t/xml, t/json) for feed1 only
        self.assertEqual(mock_cache_tag.call_count, 3)

    @patch("core.management.commands.update_feeds.wait")
    @patch("core.management.commands.update_feeds.task_manager")
    def test_update_multiple_feeds_timeout(self, mock_tm, mock_wait):
        """If wait returns not_done set, should still proceed without raising."""
        mock_future = self._create_mock_future()
        mock_tm.submit_task.return_value = mock_future

        # Wait returns one future in not_done to simulate timeout
        mock_wait.return_value = (set(), {mock_future})

        # Should not raise
        cmd.update_multiple_feeds([self.feed1])

        # Verify task was submitted
        mock_tm.submit_task.assert_called_once()
        # Verify wait was called with timeout
        mock_wait.assert_called_once_with([mock_future], timeout=1800)

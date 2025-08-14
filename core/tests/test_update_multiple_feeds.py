from django.test import TestCase
from unittest import mock
from concurrent.futures import Future

from core.management.commands import update_feeds as cmd
from core.models import Feed, Tag


class UpdateMultipleFeedsTests(TestCase):
    """Unit tests for update_multiple_feeds helper."""

    def _dummy_future(self, result=True):
        fut = Future()
        fut.set_result(result)
        return fut

    @mock.patch("core.management.commands.update_feeds.cache_tag")
    @mock.patch("core.management.commands.update_feeds.cache_rss")
    @mock.patch("core.management.commands.update_feeds.wait")
    @mock.patch("core.management.commands.update_feeds.task_manager")
    def test_update_multiple_feeds_basic(
        self, mock_tm, mock_wait, mock_cache_rss, mock_cache_tag
    ):
        """Ensure submit_task called and caching functions called for each feed."""
        # Prepare feeds with tags
        tag = Tag.objects.create(name="Tech")
        f1 = Feed.objects.create(feed_url="https://a.com/rss.xml", name="A")
        f1.tags.add(tag)
        f2 = Feed.objects.create(feed_url="https://b.com/rss.xml", name="B")
        # no tag for f2

        # mock submit_task to return completed future
        mock_tm.submit_task.side_effect = lambda *a, **k: self._dummy_future()
        # mock wait returns all in done
        mock_wait.return_value = ({self._dummy_future()}, set())

        cmd.update_multiple_feeds([f1, f2])

        # Two tasks submitted
        self.assertEqual(mock_tm.submit_task.call_count, 2)
        # cache_rss called 4 times per feed
        self.assertEqual(mock_cache_rss.call_count, 4 * 2)
        # cache_tag should be called three times (o/xml, t/xml, t/json)
        self.assertEqual(mock_cache_tag.call_count, 3)

    @mock.patch("core.management.commands.update_feeds.wait")
    @mock.patch("core.management.commands.update_feeds.task_manager")
    def test_update_multiple_feeds_timeout(self, mock_tm, mock_wait):
        """If wait returns not_done set, should still proceed without raising."""
        f = Feed.objects.create(feed_url="https://c.com/rss.xml", name="C")
        mock_tm.submit_task.return_value = self._dummy_future()
        # Wait returns one future in not_done to simulate timeout
        mock_wait.return_value = (set(), {self._dummy_future()})
        # Should not raise
        cmd.update_multiple_feeds([f])

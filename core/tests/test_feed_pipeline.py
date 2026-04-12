from unittest.mock import patch

from django.test import TestCase

from core.models import Feed


class FeedPipelineTests(TestCase):
    def setUp(self):
        self.feed = Feed.objects.create(
            feed_url="https://example.com/pipeline.xml",
            name="Pipeline Feed",
            translate_title=True,
            translate_content=True,
            summary=True,
        )

    @patch("core.services.feed.pipeline.close_old_connections")
    @patch("core.services.feed.pipeline.handle_feeds_summary")
    @patch("core.services.feed.pipeline.handle_feeds_translation")
    @patch("core.services.feed.pipeline.handle_single_feed_fetch")
    def test_run_feed_update_executes_requested_steps_in_order(
        self,
        mock_fetch,
        mock_translate,
        mock_summary,
        mock_close_connections,
    ):
        from core.services.feed import run_feed_update

        self.assertTrue(run_feed_update(self.feed))

        self.assertEqual(
            [call.args for call in mock_translate.call_args_list],
            [
                ([self.feed],),
                ([self.feed],),
            ],
        )
        self.assertEqual(
            [call.kwargs for call in mock_translate.call_args_list],
            [
                {"target_field": "title"},
                {"target_field": "content"},
            ],
        )
        mock_fetch.assert_called_once_with(self.feed)
        mock_summary.assert_called_once_with([self.feed])
        self.assertGreaterEqual(mock_close_connections.call_count, 2)

    @patch("core.services.feed.pipeline.close_old_connections")
    @patch("core.services.feed.pipeline.handle_single_feed_fetch")
    @patch("core.services.feed.pipeline.logger")
    def test_run_feed_update_returns_false_when_fetch_fails(
        self,
        mock_logger,
        mock_fetch,
        mock_close_connections,
    ):
        from core.services.feed import run_feed_update

        mock_fetch.side_effect = RuntimeError("boom")

        self.assertFalse(run_feed_update(self.feed))
        mock_logger.exception.assert_called_once()
        self.assertGreaterEqual(mock_close_connections.call_count, 2)

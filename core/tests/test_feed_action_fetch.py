from django.test import SimpleTestCase
from unittest import mock

from utils.feed_action import fetch_feed


class DummyFeed:
    """Minimal object mimicking feedparser result."""

    def __init__(self, status=200, bozo=False, entries=None):
        self.status = status
        self.bozo = bozo
        self.entries = [] if entries is None else entries

    def get(self, key, default=None):
        return default


class FetchFeedTests(SimpleTestCase):
    """Unit tests for utils.feed_action.fetch_feed with mock feedparser."""

    @mock.patch("utils.feed_action.manual_fetch_feed")
    @mock.patch("utils.feed_action.feedparser.parse")
    def test_fetch_scenarios(self, mock_parse, mock_manual):
        """Test different fetch scenarios including 304, bozo feeds, and normal success."""
        # Test 304 not modified
        mock_parse.return_value = DummyFeed(status=304)
        result = fetch_feed("https://example.com/rss.xml", etag="abc")
        self.assertFalse(result["update"])
        self.assertIsNone(result["feed"])
        self.assertIsNone(result["error"])
        
        # Test bozo feed triggers manual fetch
        dummy = DummyFeed(status=200, bozo=True, entries=[])
        mock_parse.return_value = dummy
        manual_return = {"feed": "manual", "update": True, "error": None}
        mock_manual.return_value = manual_return
        
        result = fetch_feed("https://example.com/rss.xml")
        mock_manual.assert_called_once()
        self.assertEqual(result, manual_return)
        
        # Test normal success
        mock_manual.reset_mock()
        mock_parse.return_value = DummyFeed(status=200, bozo=False)
        result = fetch_feed("https://example.com/rss.xml")
        self.assertTrue(result["update"])
        self.assertIs(result["feed"], mock_parse.return_value)
        self.assertIsNone(result["error"])
        mock_manual.assert_not_called()

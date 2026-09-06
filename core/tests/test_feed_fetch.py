from django.test import SimpleTestCase
from unittest import mock

from core.tasks.fetch_feeds import FALLBACK_USER_AGENT, fetch_feed, manual_fetch_feed


class DummyFeed:
    """Minimal object mimicking feedparser result."""

    def __init__(self, status=200, bozo=False, entries=None):
        self.status = status
        self.bozo = bozo
        self.entries = [] if entries is None else entries

    def get(self, key, default=None):
        return default


class FetchFeedTests(SimpleTestCase):
    """Unit tests for core.tasks.fetch_feeds.fetch_feed with mock feedparser."""

    @mock.patch("core.tasks.fetch_feeds.manual_fetch_feed")
    @mock.patch("core.tasks.fetch_feeds.feedparser.parse")
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

    @mock.patch("core.tasks.fetch_feeds.manual_fetch_feed")
    @mock.patch("core.tasks.fetch_feeds.feedparser.parse")
    def test_fetch_feed_exception_handling(self, mock_parse, mock_manual):
        """Test fetch_feed exception handling."""
        # Test exception during feedparser.parse
        mock_parse.side_effect = Exception("Network error")

        result = fetch_feed("https://example.com/rss.xml")
        self.assertFalse(result["update"])
        self.assertIsNone(result["feed"])
        self.assertEqual(result["error"], "Network error")

        # Test with different exception types
        mock_parse.side_effect = ValueError("Invalid URL")
        result = fetch_feed("https://example.com/rss.xml")
        self.assertFalse(result["update"])
        self.assertIsNone(result["feed"])
        self.assertEqual(result["error"], "Invalid URL")

    @mock.patch("core.tasks.fetch_feeds.get_fetch_user_agent", return_value="test-UA")
    @mock.patch("core.tasks.fetch_feeds.manual_fetch_feed")
    @mock.patch("core.tasks.fetch_feeds.feedparser.parse")
    def test_manual_fetch_reuses_same_user_agent(
        self, mock_parse, mock_manual, mock_ua
    ):
        """The UA used by feedparser is passed through to manual_fetch_feed."""
        dummy = DummyFeed(status=403, bozo=True, entries=[])
        mock_parse.return_value = dummy
        manual_return = {"feed": "manual", "update": True, "error": None}
        mock_manual.return_value = manual_return

        result = fetch_feed("https://example.com/rss.xml", etag="abc")

        self.assertEqual(result, manual_return)
        mock_ua.assert_called_once_with()
        mock_parse.assert_called_once_with(
            "https://example.com/rss.xml", etag="abc", agent="test-UA"
        )
        mock_manual.assert_called_once_with(
            "https://example.com/rss.xml", "abc", user_agent="test-UA"
        )

    @mock.patch("core.tasks.fetch_feeds.manual_fetch_feed")
    @mock.patch("core.tasks.fetch_feeds.feedparser.parse")
    def test_fetch_feed_with_bozo_exception(self, mock_parse, mock_manual):
        """Test fetch_feed with bozo feed that has exception."""
        # Test bozo feed with exception
        dummy = DummyFeed(status=200, bozo=True, entries=[])
        dummy.get = (
            lambda key, default=None: "bozo exception"
            if key == "bozo_exception"
            else default
        )
        mock_parse.return_value = dummy

        manual_return = {"feed": "manual", "update": True, "error": None}
        mock_manual.return_value = manual_return

        result = fetch_feed("https://example.com/rss.xml")
        mock_manual.assert_called_once()
        self.assertEqual(result, manual_return)


class ManualFetchFeedTests(SimpleTestCase):
    """manual_fetch_feed header construction, especially the nullable etag."""

    VALID_FEED = "<rss version='2.0'><channel><title>t</title></channel></rss>"

    def _response(self, status=200, text=VALID_FEED):
        response = mock.Mock(status_code=status, text=text)
        response.raise_for_status = mock.Mock()
        return response

    @mock.patch("httpx.Client")
    def test_null_etag_omits_if_none_match(self, mock_client_cls):
        """A NULL etag from the DB must not end up in the httpx headers."""
        client = mock_client_cls.return_value.__enter__.return_value
        client.get.return_value = self._response()

        result = manual_fetch_feed(
            "https://example.com/rss.xml", etag=None, user_agent="test-UA"
        )

        headers = client.get.call_args.kwargs["headers"]
        self.assertNotIn("If-None-Match", headers)
        self.assertIsNone(result["error"])
        self.assertTrue(result["update"])

    @mock.patch("httpx.Client")
    def test_empty_etag_omits_if_none_match(self, mock_client_cls):
        client = mock_client_cls.return_value.__enter__.return_value
        client.get.return_value = self._response()

        manual_fetch_feed("https://example.com/rss.xml", etag="", user_agent="test-UA")

        headers = client.get.call_args.kwargs["headers"]
        self.assertNotIn("If-None-Match", headers)

    @mock.patch("httpx.Client")
    def test_etag_sets_if_none_match(self, mock_client_cls):
        client = mock_client_cls.return_value.__enter__.return_value
        client.get.return_value = self._response()

        manual_fetch_feed(
            "https://example.com/rss.xml", etag='"abc"', user_agent="test-UA"
        )

        headers = client.get.call_args.kwargs["headers"]
        self.assertEqual(headers["If-None-Match"], '"abc"')

    @mock.patch("httpx.Client")
    def test_fallback_request_omits_null_etag(self, mock_client_cls):
        """The 403 fallback retry must not send a null If-None-Match either."""
        client = mock_client_cls.return_value.__enter__.return_value
        client.get.side_effect = [self._response(status=403, text=""), self._response()]

        result = manual_fetch_feed(
            "https://example.com/rss.xml", etag=None, user_agent="browser-UA"
        )

        self.assertEqual(client.get.call_count, 2)
        fallback_headers = client.get.call_args_list[1].kwargs["headers"]
        self.assertEqual(fallback_headers["User-Agent"], FALLBACK_USER_AGENT)
        self.assertNotIn("If-None-Match", fallback_headers)
        self.assertTrue(result["update"])

    @mock.patch("httpx.Client")
    def test_fallback_request_keeps_etag(self, mock_client_cls):
        client = mock_client_cls.return_value.__enter__.return_value
        client.get.side_effect = [self._response(status=403, text=""), self._response()]

        manual_fetch_feed(
            "https://example.com/rss.xml", etag='"abc"', user_agent="browser-UA"
        )

        fallback_headers = client.get.call_args_list[1].kwargs["headers"]
        self.assertEqual(fallback_headers["If-None-Match"], '"abc"')

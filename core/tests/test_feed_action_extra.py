import time
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase
from django.utils import timezone

from core.cache import (
    _build_atom_feed,
    _add_atom_entry,
    _finalize_atom_feed,
)
from core.tasks.fetch_feeds import (
    convert_struct_time_to_datetime,
    manual_fetch_feed,
)

class ConvertStructTimeTests(SimpleTestCase):
    """Unit tests for convert_struct_time_to_datetime helper."""

    def test_conversion_cases(self):
        """Test epoch conversion and None handling."""
        # Test epoch time conversion
        dt = convert_struct_time_to_datetime(time.gmtime(0))
        self.assertEqual(dt.year, 1970)
        self.assertIsNotNone(dt.tzinfo)

        # Test None handling
        self.assertIsNone(convert_struct_time_to_datetime(None))


class ManualFetchFeedTests(SimpleTestCase):
    """Isolated tests for core.tasks.fetch_feeds.manual_fetch_feed."""

    def setUp(self):
        self.mock_patches = [
            mock.patch("core.tasks.fetch_feeds.UserAgent"),
            mock.patch("httpx.Client"),
            mock.patch("core.tasks.fetch_feeds.feedparser.parse"),
        ]
        self.mock_useragent, self.mock_client_cls, self.mock_parse = [
            p.start() for p in self.mock_patches
        ]
        self.mock_useragent.return_value.random = "UA"
        self.mock_client = mock.Mock()
        self.mock_client_cls.return_value = self.mock_client

    def tearDown(self):
        for p in self.mock_patches:
            p.stop()

    def test_http_responses(self):
        """Test different HTTP response scenarios."""
        # Test 200 success
        mock_response = mock.Mock(status_code=200, text="<rss></rss>")
        self.mock_client.get.return_value = mock_response
        dummy_feed = SimpleNamespace(
            bozo=False, entries=["item"], get=lambda *a, **k: None
        )
        self.mock_parse.return_value = dummy_feed

        result = manual_fetch_feed("http://example.com/rss", etag="abc")
        self.assertTrue(result["update"])
        self.assertIs(result["feed"], dummy_feed)
        self.assertIsNone(result["error"])

        # Test 304 not modified
        mock_response.status_code = 304
        mock_response.text = ""
        self.mock_parse.reset_mock()

        result = manual_fetch_feed("http://example.com/rss", etag="abc")
        self.assertFalse(result["update"])
        self.assertEqual(result["feed"], {})
        self.assertIsNone(result["error"])
        self.mock_parse.assert_not_called()

        # Test HTTP error
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = Exception("server error")

        result = manual_fetch_feed("http://example.com/rss")
        self.assertFalse(result["update"])
        self.assertEqual(result["feed"], {})
        self.assertIn("server error", result["error"])

    def test_exception_handling(self):
        """Test exception handling in manual_fetch_feed."""
        # Test HTTPStatusError
        mock_response = mock.Mock(
            status_code=500, reason_phrase="Internal Server Error"
        )
        mock_response.raise_for_status.side_effect = Exception("HTTP status error")
        self.mock_client.get.return_value = mock_response

        result = manual_fetch_feed("http://example.com/rss")
        self.assertFalse(result["update"])
        self.assertEqual(result["feed"], {})
        self.assertIn("HTTP status error", result["error"])

        # Test TimeoutException
        self.mock_client.get.side_effect = Exception("Timeout")
        result = manual_fetch_feed("http://example.com/rss")
        self.assertFalse(result["update"])
        self.assertEqual(result["feed"], {})
        self.assertIn("Timeout", result["error"])

        # Test general exception
        self.mock_client.get.side_effect = Exception("General error")
        result = manual_fetch_feed("http://example.com/rss")
        self.assertFalse(result["update"])
        self.assertEqual(result["feed"], {})
        self.assertIn("General error", result["error"])

    def test_specific_exception_types(self):
        """Test specific exception types in manual_fetch_feed."""
        # Test httpx.HTTPStatusError
        mock_response = mock.Mock(
            status_code=500, reason_phrase="Internal Server Error"
        )
        mock_response.raise_for_status.side_effect = Exception("HTTP status error")
        self.mock_client.get.return_value = mock_response

        result = manual_fetch_feed("http://example.com/rss")
        self.assertFalse(result["update"])
        self.assertEqual(result["feed"], {})
        self.assertIn("HTTP status error", result["error"])

        # Test httpx.TimeoutException
        self.mock_client.get.side_effect = Exception("Timeout")
        result = manual_fetch_feed("http://example.com/rss")
        self.assertFalse(result["update"])
        self.assertEqual(result["feed"], {})
        self.assertIn("Timeout", result["error"])

    def test_httpx_specific_exceptions(self):
        """Test httpx specific exception types to cover lines 67 and 69."""
        # Mock httpx exceptions
        import httpx

        # Test httpx.HTTPStatusError
        mock_response = mock.Mock(
            status_code=500, reason_phrase="Internal Server Error"
        )
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "HTTP status error", request=mock.Mock(), response=mock_response
        )
        self.mock_client.get.return_value = mock_response

        result = manual_fetch_feed("http://example.com/rss")
        self.assertFalse(result["update"])
        self.assertEqual(result["feed"], {})
        self.assertIn("HTTP status error", result["error"])

        # Test httpx.TimeoutException
        self.mock_client.get.side_effect = httpx.TimeoutException("Timeout")
        result = manual_fetch_feed("http://example.com/rss")
        self.assertFalse(result["update"])
        self.assertEqual(result["feed"], {})
        self.assertIn("Timeout", result["error"])

    def test_bozo_feed_with_exception(self):
        """Test handling of bozo feed with exception."""
        mock_response = mock.Mock(status_code=200, text="<rss></rss>")
        self.mock_client.get.return_value = mock_response

        # Test bozo feed with exception
        dummy_feed = SimpleNamespace(
            bozo=True,
            entries=[],
            get=lambda key, default=None: "bozo exception"
            if key == "bozo_exception"
            else default,
        )
        self.mock_parse.return_value = dummy_feed

        result = manual_fetch_feed("http://example.com/rss")
        self.assertTrue(result["update"])  # update is still True for 200 status
        self.assertEqual(result["feed"], dummy_feed)
        self.assertEqual(result["error"], "bozo exception")


class AtomFeedTests(SimpleTestCase):
    """Tests for atom feed building, entry addition and finalization."""

    @mock.patch(
        "core.cache.set_translation_display", lambda o, t, *_args, **_kw: f"{t}"
    )
    def test_atom_feed_operations(self):
        """Test building atom feed with entry and finalization."""
        now = timezone.now()
        fg = _build_atom_feed(
            feed_id="urn:test-feed",
            title="Test Feed",
            author="Tester",
            link="http://example.com",
            subtitle="Sub",
            language="en",
            updated=now,
            pubdate=now,
        )

        entry_obj = SimpleNamespace(
            pubdate=now,
            updated=None,
            original_summary="Orig summary",
            original_title="Orig title",
            original_content="Orig content",
            translated_title="Tran title",
            translated_content="Tran content",
            ai_summary="AI sum",
            feed=SimpleNamespace(translation_display=1),
            link="http://example.com/post",
            author="Author",
            guid="GUID123",
            id=1,
            enclosures_xml='<enclosures><enclosure href="http://file.mp3" type="audio/mpeg" length="123"/></enclosures>',
        )

        fe = _add_atom_entry(fg, entry_obj, feed_type="t")
        self.assertIn("Tran title", fe.title())
        self.assertIn(
            "🤖",
            fe.content()["content"] if isinstance(fe.content(), dict) else fe.content(),
        )

        xml_str = _finalize_atom_feed(fg)
        self.assertIn("file.mp3", xml_str)
        self.assertIn("rss.xsl", xml_str)
        self.assertIn("Tran title", xml_str)

    def test_enclosures_parsing_error(self):
        """Test handling of enclosures parsing errors."""
        now = timezone.now()
        fg = _build_atom_feed(
            feed_id="urn:test-feed",
            title="Test Feed",
            author="Tester",
            link="http://example.com",
            subtitle="Sub",
            language="en",
            updated=now,
            pubdate=now,
        )

        # Test with invalid XML that will cause parsing error
        entry_obj = SimpleNamespace(
            pubdate=now,
            updated=None,
            original_summary="Orig summary",
            original_title="Orig title",
            original_content="Orig content",
            translated_title=None,
            translated_content=None,
            ai_summary=None,
            feed=SimpleNamespace(translation_display=1),
            link="http://example.com/post",
            author="Author",
            guid="GUID123",
            id=1,
            enclosures_xml="<invalid><xml>",
        )

        # This should not raise an exception due to try-catch
        fe = _add_atom_entry(fg, entry_obj, feed_type="o")
        self.assertIsNotNone(fe)

        # Test with None enclosures_xml
        entry_obj.enclosures_xml = None
        fe = _add_atom_entry(fg, entry_obj, feed_type="o")
        self.assertIsNotNone(fe)

        # Test with empty enclosures_xml
        entry_obj.enclosures_xml = ""
        fe = _add_atom_entry(fg, entry_obj, feed_type="o")
        self.assertIsNotNone(fe)

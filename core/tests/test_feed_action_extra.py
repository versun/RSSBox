import time
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase
from django.utils import timezone

from utils.feed_action import (
    convert_struct_time_to_datetime,
    manual_fetch_feed,
    _build_atom_feed,
    _add_atom_entry,
    _finalize_atom_feed,
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
    """Isolated tests for utils.feed_action.manual_fetch_feed."""

    def setUp(self):
        self.mock_patches = [
            mock.patch("utils.feed_action.UserAgent"),
            mock.patch("httpx.Client"),
            mock.patch("utils.feed_action.feedparser.parse")
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
        dummy_feed = SimpleNamespace(bozo=False, entries=["item"], get=lambda *a, **k: None)
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


class AtomFeedTests(SimpleTestCase):
    """Tests for atom feed building, entry addition and finalization."""

    @mock.patch("utils.feed_action.set_translation_display", lambda o, t, *_args, **_kw: f"{t}")
    def test_atom_feed_operations(self):
        """Test building atom feed with entry and finalization."""
        now = timezone.now()
        fg = _build_atom_feed(
            feed_id="urn:test-feed", title="Test Feed", author="Tester",
            link="http://example.com", subtitle="Sub", language="en",
            updated=now, pubdate=now
        )

        entry_obj = SimpleNamespace(
            pubdate=now, updated=None, original_summary="Orig summary",
            original_title="Orig title", original_content="Orig content",
            translated_title="Tran title", translated_content="Tran content",
            ai_summary="AI sum", feed=SimpleNamespace(translation_display=1),
            link="http://example.com/post", author="Author", guid="GUID123", id=1,
            enclosures_xml='<enclosures><enclosure href="http://file.mp3" type="audio/mpeg" length="123"/></enclosures>'
        )

        fe = _add_atom_entry(fg, entry_obj, feed_type="t")
        self.assertIn("Tran title", fe.title())
        self.assertIn("🤖", fe.content()["content"] if isinstance(fe.content(), dict) else fe.content())
        
        xml_str = _finalize_atom_feed(fg)
        self.assertIn("file.mp3", xml_str)
        self.assertIn("rss.xsl", xml_str)
        self.assertIn("Tran title", xml_str)

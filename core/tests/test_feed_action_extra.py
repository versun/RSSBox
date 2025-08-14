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

    def test_epoch_conversion(self):
        """Epoch time (0) should convert to 1970-01-01 with tzinfo."""
        dt = convert_struct_time_to_datetime(time.gmtime(0))
        self.assertEqual(dt.year, 1970)
        self.assertIsNotNone(dt.tzinfo)

    def test_none_returns_none(self):
        """None input should be returned as None (graceful handling)."""
        self.assertIsNone(convert_struct_time_to_datetime(None))


class ManualFetchFeedTests(SimpleTestCase):
    """Isolated tests for utils.feed_action.manual_fetch_feed."""

    @mock.patch("utils.feed_action.feedparser.parse")
    @mock.patch("utils.feed_action.UserAgent")
    @mock.patch("httpx.Client")
    def test_success_200(self, mock_client_cls, mock_useragent_cls, mock_parse):
        # Stub UserAgent.random
        mock_useragent_cls.return_value.random = "UA"
        # Fake HTTP 200 response object
        mock_response = mock.Mock()
        mock_response.status_code = 200
        mock_response.text = "<rss></rss>"

        mock_client = mock.Mock()
        mock_client.get.return_value = mock_response
        mock_client_cls.return_value = mock_client

        dummy_feed = SimpleNamespace(bozo=False, entries=["item"], get=lambda *a, **k: None)
        mock_parse.return_value = dummy_feed

        result = manual_fetch_feed("http://example.com/rss", etag="abc")
        self.assertTrue(result["update"])
        self.assertIs(result["feed"], dummy_feed)
        self.assertIsNone(result["error"])

    @mock.patch("utils.feed_action.feedparser.parse")
    @mock.patch("utils.feed_action.UserAgent")
    @mock.patch("httpx.Client")
    def test_not_modified_304(self, mock_client_cls, mock_useragent_cls, mock_parse):
        mock_useragent_cls.return_value.random = "UA"
        mock_response = mock.Mock()
        mock_response.status_code = 304
        mock_response.text = ""
        mock_client = mock.Mock()
        mock_client.get.return_value = mock_response
        mock_client_cls.return_value = mock_client

        # feedparser.parse should never be called when 304
        result = manual_fetch_feed("http://example.com/rss", etag="abc")
        self.assertFalse(result["update"])
        self.assertEqual(result["feed"], {})
        self.assertIsNone(result["error"])
        mock_parse.assert_not_called()

    @mock.patch("utils.feed_action.UserAgent")
    @mock.patch("httpx.Client")
    def test_http_error(self, mock_client_cls, mock_useragent_cls):
        mock_useragent_cls.return_value.random = "UA"
        # Simulate HTTP 500 that triggers raise_for_status
        mock_response = mock.Mock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = Exception("server error")
        mock_client = mock.Mock()
        mock_client.get.return_value = mock_response
        mock_client_cls.return_value = mock_client

        result = manual_fetch_feed("http://example.com/rss")
        self.assertFalse(result["update"])
        self.assertEqual(result["feed"], {})
        self.assertIn("server error", result["error"])


class AddAtomEntryTests(SimpleTestCase):
    """Tests covering _build_atom_feed, _add_atom_entry and _finalize_atom_feed."""

    @mock.patch("utils.feed_action.set_translation_display", lambda o, t, *_args, **_kw: f"{t}")
    def test_add_entry_with_translation_and_summary(self):
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

        dummy_feed = SimpleNamespace(translation_display=1)
        entry_obj = SimpleNamespace(
            pubdate=now,
            updated=None,
            original_summary="Orig summary",
            original_title="Orig title",
            original_content="Orig content",
            translated_title="Tran title",
            translated_content="Tran content",
            ai_summary="AI sum",
            feed=dummy_feed,
            link="http://example.com/post",
            author="Author",
            guid="GUID123",
            enclosures_xml='<enclosures><enclosure href="http://file.mp3" type="audio/mpeg" length="123"/></enclosures>',
            id=1,
        )

        fe = _add_atom_entry(fg, entry_obj, feed_type="t")
        # Title should have used translated_title due to patched set_translation_display
        self.assertIn("Tran title", fe.title())
        # AI summary should be in content
        self.assertIn("🤖", fe.content()["content"] if isinstance(fe.content(), dict) else fe.content())
        # Ensure enclosure reflected in final XML
        xml_str = _finalize_atom_feed(fg)
        self.assertIn("file.mp3", xml_str)

        # Finalize feed returns valid XML with stylesheet PI
        self.assertIn("rss.xsl", xml_str)
        self.assertIn("Tran title", xml_str)

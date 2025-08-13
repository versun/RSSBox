import time
from django.test import SimpleTestCase
from django.utils.safestring import SafeString
from django.utils import timezone

from utils.modelAdmin_utils import status_icon
from utils.feed_action import convert_struct_time_to_datetime, generate_atom_feed
from core.models import Feed


class ModelAdminUtilsTests(SimpleTestCase):
    """Unit tests for utils.modelAdmin_utils helper functions."""

    def test_status_icon_loading(self):
        self.assertIn("icon-loading.svg", str(status_icon(None)))

    def test_status_icon_success(self):
        html = status_icon(True)
        self.assertIsInstance(html, SafeString)
        self.assertIn("icon-yes.svg", str(html))

    def test_status_icon_error(self):
        self.assertIn("icon-no.svg", str(status_icon(False)))


from django.test import TestCase


class FeedActionUtilityTests(TestCase):
    """Tests for lightweight helpers in utils.feed_action."""

    def test_convert_struct_time_to_datetime(self):
        struct_t = time.gmtime(0)  # Epoch
        dt = convert_struct_time_to_datetime(struct_t)
        # Should convert to aware datetime in local timezone and match epoch
        self.assertEqual(int(dt.timestamp()), 0)
        # None returns None
        self.assertIsNone(convert_struct_time_to_datetime(None))

    def test_generate_atom_feed_no_entries(self):
        """generate_atom_feed should still return XML when feed has no entries."""
        feed = Feed.objects.create(feed_url="https://example.com/rss.xml", name="Test")
        xml = generate_atom_feed(feed, feed_type="o")
        self.assertIsInstance(xml, str)
        self.assertTrue(xml.startswith("<?xml"))
        # Should include feed id
        self.assertIn(str(feed.id), xml)

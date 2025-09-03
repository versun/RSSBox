import time
from django.test import SimpleTestCase, TestCase
from django.utils.safestring import SafeString

from utils.modelAdmin_utils import status_icon
from core.cache import generate_atom_feed
from core.tasks.fetch_feeds import convert_struct_time_to_datetime
from core.models import Feed


class AdminUtilsTests(SimpleTestCase):
    """Unit tests for admin utility functions."""

    def test_status_icon_states(self):
        """Test status_icon for different states."""
        # Test loading state
        self.assertIn("icon-loading.svg", str(status_icon(None)))

        # Test success state
        html = status_icon(True)
        self.assertIsInstance(html, SafeString)
        self.assertIn("icon-yes.svg", str(html))

        # Test error state
        self.assertIn("icon-no.svg", str(status_icon(False)))


class FeedActionUtilityTests(TestCase):
    """Tests for feed action utility functions."""

    def test_utility_functions(self):
        """Test convert_struct_time_to_datetime and generate_atom_feed."""
        # Test time conversion
        struct_t = time.gmtime(0)
        dt = convert_struct_time_to_datetime(struct_t)
        self.assertEqual(int(dt.timestamp()), 0)
        self.assertIsNone(convert_struct_time_to_datetime(None))

        # Test atom feed generation
        feed = Feed.objects.create(feed_url="https://example.com/rss.xml", name="Test")
        xml = generate_atom_feed(feed, feed_type="o")
        self.assertIsInstance(xml, str)
        self.assertTrue(xml.startswith("<?xml"))
        self.assertIn(str(feed.id), xml)

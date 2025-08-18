from django.test import TestCase
from core.models import Feed, Entry


class EntryModelTestCase(TestCase):
    """Test cases for Entry model"""

    def setUp(self):
        self.feed = Feed.objects.create(
            name="Test Feed",
            feed_url="https://example.com/feed.xml",
            target_language="zh-CN",
        )

    def test_entry_str_method(self):
        """Test Entry __str__ method with different title values."""
        # Test with normal title
        entry = Entry.objects.create(
            feed=self.feed,
            link="https://example.com/article",
            original_title="Test Article Title",
        )
        self.assertEqual(str(entry), "Test Article Title")

        # Test with empty title
        entry_empty = Entry.objects.create(
            feed=self.feed, link="https://example.com/article2", original_title=""
        )
        self.assertEqual(str(entry_empty), "")

from django.test import TestCase
from core.models import Feed, Entry


class EntryModelTestCase(TestCase):
    """Test cases for Entry model"""

    def setUp(self):
        self.feed = Feed.objects.create(
            name="Test Feed",
            feed_url="https://example.com/feed.xml",
            target_language="zh-CN"
        )

    def test_entry_str_method(self):
        """Test Entry __str__ method (line 23)."""
        entry = Entry.objects.create(
            feed=self.feed,
            link="https://example.com/article",
            original_title="Test Article Title"
        )
        
        result = str(entry)
        self.assertEqual(result, "Test Article Title")

    def test_entry_str_method_empty_title(self):
        """Test Entry __str__ method with empty title."""
        entry = Entry.objects.create(
            feed=self.feed,
            link="https://example.com/article", 
            original_title=""
        )
        
        result = str(entry)
        self.assertEqual(result, "")

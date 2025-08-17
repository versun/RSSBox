from django.test import TestCase
from django.utils import timezone

from utils.feed_action import generate_atom_feed, merge_feeds_into_one_atom
from core.models import Feed, Entry, Tag


class FeedActionAtomTests(TestCase):
    """Tests covering generate_atom_feed and merge_feeds_into_one_atom."""

    def setUp(self):
        self.tag = Tag.objects.create(name="Demo Tag")
        self.base_entry_data = {
            "link": "https://example.com/post1",
            "original_title": "Original Title",
            "original_content": "Original Content",
            "pubdate": timezone.now(),
        }

    def _create_feed_with_entry(self, translated: bool = False, url_suffix: str = ""):
        feed = Feed.objects.create(
            feed_url=f"https://example.com/rss{url_suffix}.xml", 
            name="Test Feed"
        )
        feed.tags.add(self.tag)

        entry_data = self.base_entry_data.copy()
        entry_data["feed"] = feed
        
        if translated:
            entry_data.update({
                "translated_title": "翻译标题",
                "translated_content": "翻译内容"
            })
            feed.translate_title = True
            feed.translate_content = True
            feed.save()

        Entry.objects.create(**entry_data)
        return feed

    def test_generate_atom_feed_types(self):
        """Test atom feed generation for original and translated types."""
        # Test original feed
        feed_orig = self._create_feed_with_entry(translated=False, url_suffix="orig")
        xml_orig = generate_atom_feed(feed_orig, feed_type="o")
        self.assertIn("Original Title", xml_orig)
        self.assertNotIn("翻译标题", xml_orig)
        
        # Test translated feed
        feed_trans = self._create_feed_with_entry(translated=True, url_suffix="trans")
        xml_trans = generate_atom_feed(feed_trans, feed_type="t")
        self.assertIn("翻译标题", xml_trans)

    def test_merge_feeds_into_one_atom(self):
        """Test merging multiple feeds into one atom feed."""
        f1 = self._create_feed_with_entry(translated=False, url_suffix="1")
        f2 = self._create_feed_with_entry(translated=False, url_suffix="2")
        xml = merge_feeds_into_one_atom(self.tag.slug, [f1, f2], feed_type="o")
        self.assertGreater(xml.count("Original Title"), 1)

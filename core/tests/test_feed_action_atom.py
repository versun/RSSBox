from django.test import TestCase
from django.utils import timezone

from utils.feed_action import generate_atom_feed, merge_feeds_into_one_atom
from core.models import Feed, Entry, Tag


class FeedActionAtomTests(TestCase):
    """Tests covering generate_atom_feed and merge_feeds_into_one_atom."""

    def setUp(self):
        # Common tag for merged feed tests
        self.tag = Tag.objects.create(name="Demo Tag")

    def _create_feed_with_entry(self, translated: bool = False, url_suffix: str = ""):
        feed_url = f"https://example.com/rss{url_suffix}.xml"
        feed = Feed.objects.create(feed_url=feed_url, name="Test Feed")
        feed.tags.add(self.tag)

        entry_kwargs = {
            "feed": feed,
            "link": "https://example.com/post1",
            "original_title": "Original Title",
            "original_content": "Original Content",
            "pubdate": timezone.now(),
        }
        if translated:
            entry_kwargs["translated_title"] = "翻译标题"
            entry_kwargs["translated_content"] = "翻译内容"
            # activate translation flags so generate_atom_feed treats as translated feed
            feed.translate_title = True
            feed.translate_content = True
            feed.save()

        Entry.objects.create(**entry_kwargs)
        return feed

    def test_generate_atom_feed_original(self):
        """XML for original feed should include original title only."""
        feed = self._create_feed_with_entry(translated=False)
        xml = generate_atom_feed(feed, feed_type="o")
        self.assertIn("Original Title", xml)
        self.assertNotIn("翻译标题", xml)

    def test_generate_atom_feed_translated(self):
        """Translated feed XML should contain translated title when flags enabled."""
        feed = self._create_feed_with_entry(translated=True)
        xml = generate_atom_feed(feed, feed_type="t")
        self.assertIn("翻译标题", xml)

    def test_merge_feeds_into_one_atom(self):
        """Merged atom feed should combine entries from multiple feeds."""
        f1 = self._create_feed_with_entry(translated=False, url_suffix="1")
        f2 = self._create_feed_with_entry(translated=False, url_suffix="2")
        xml = merge_feeds_into_one_atom(self.tag.slug, [f1, f2], feed_type="o")
        # Expect both original titles appear twice (could be same text)
        self.assertGreater(xml.count("Original Title"), 1)

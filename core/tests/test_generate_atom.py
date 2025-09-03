from django.test import TestCase
from django.utils import timezone

from core.cache import generate_atom_feed, merge_feeds_into_one_atom
from core.models import Feed, Entry, Tag


class GenerateAtomTests(TestCase):
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
            feed_url=f"https://example.com/rss{url_suffix}.xml", name="Test Feed"
        )
        feed.tags.add(self.tag)

        entry_data = self.base_entry_data.copy()
        entry_data["feed"] = feed

        if translated:
            entry_data.update(
                {"translated_title": "翻译标题", "translated_content": "翻译内容"}
            )
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

    def test_generate_atom_feed_with_none_feed(self):
        """Test generate_atom_feed with None feed."""
        result = generate_atom_feed(None)
        self.assertIsNone(result)

    def test_generate_atom_feed_with_none_entries(self):
        """Test generate_atom_feed when filtered_entries is None."""
        feed = Feed.objects.create(
            feed_url="https://example.com/rss_none.xml", name="Test Feed None"
        )
        feed.tags.add(self.tag)

        # Test with feed that has no entries
        result = generate_atom_feed(feed, feed_type="t")
        self.assertIsNotNone(result)
        self.assertIn("Test Feed None", result)

    def test_generate_atom_feed_exception_handling(self):
        """Test generate_atom_feed exception handling."""
        feed = Feed.objects.create(
            feed_url="https://example.com/rss_exception.xml", name="Test Feed Exception"
        )
        feed.tags.add(self.tag)

        # Create an entry that will cause an exception during processing
        entry_data = self.base_entry_data.copy()
        entry_data["feed"] = feed
        entry_data["pubdate"] = None  # This might cause issues
        Entry.objects.create(**entry_data)

        # This should not raise an exception due to try-catch
        result = generate_atom_feed(feed, feed_type="t")
        self.assertIsNotNone(result)

    def test_merge_feeds_with_empty_entries(self):
        """Test merge_feeds_into_one_atom with feeds that have no entries."""
        feed = Feed.objects.create(
            feed_url="https://example.com/rss_empty.xml", name="Test Feed Empty"
        )
        feed.tags.add(self.tag)

        # Feed with no entries
        result = merge_feeds_into_one_atom(self.tag.slug, [feed], feed_type="o")
        self.assertIsNotNone(result)
        self.assertIn("Test Feed Empty", result)

    def test_merge_feeds_with_no_tag_filters(self):
        """Test merge_feeds_into_one_atom when tag has no filters."""
        # Create a tag with no filters
        tag_no_filters = Tag.objects.create(name="No Filters Tag")

        f1 = self._create_feed_with_entry(translated=False, url_suffix="no_filter_1")
        f1.tags.add(tag_no_filters)
        f2 = self._create_feed_with_entry(translated=False, url_suffix="no_filter_2")
        f2.tags.add(tag_no_filters)

        result = merge_feeds_into_one_atom(tag_no_filters.slug, [f1, f2], feed_type="o")
        self.assertIsNotNone(result)
        self.assertIn("Original Title", result)

    def test_merge_feeds_with_translated_type(self):
        """Test merge_feeds_into_one_atom with translated feed type."""
        f1 = self._create_feed_with_entry(translated=True, url_suffix="trans_1")
        f2 = self._create_feed_with_entry(translated=True, url_suffix="trans_2")

        result = merge_feeds_into_one_atom(self.tag.slug, [f1, f2], feed_type="t")
        self.assertIsNotNone(result)
        self.assertIn("Translated", result)
        self.assertIn("翻译标题", result)

    def test_generate_atom_feed_with_exception_during_processing(self):
        """Test generate_atom_feed exception handling during processing."""
        feed = Feed.objects.create(
            feed_url="https://example.com/rss_exception.xml", name="Test Feed Exception"
        )
        feed.tags.add(self.tag)

        # Create an entry that will cause an exception during processing
        entry_data = self.base_entry_data.copy()
        entry_data["feed"] = feed
        entry_data["pubdate"] = None  # This might cause issues
        Entry.objects.create(**entry_data)

        # This should not raise an exception due to try-catch
        result = generate_atom_feed(feed, feed_type="t")
        self.assertIsNotNone(result)

    def test_merge_feeds_with_empty_entries_continue_handling(self):
        """Test merge_feeds_into_one_atom continues processing when feed has no entries."""
        # Create a feed with entries
        feed_with_entries = self._create_feed_with_entry(
            translated=False, url_suffix="with_entries"
        )

        # Create a feed with no entries
        feed_no_entries = Feed.objects.create(
            feed_url="https://example.com/rss_no_entries.xml",
            name="Test Feed No Entries",
        )
        feed_no_entries.tags.add(self.tag)

        # Merge both feeds - should continue processing even with empty entries
        result = merge_feeds_into_one_atom(
            self.tag.slug, [feed_with_entries, feed_no_entries], feed_type="o"
        )
        self.assertIsNotNone(result)
        self.assertIn("Original Title", result)
        self.assertIn(
            "Test Feed No Entries", result
        )  # Feed name should be in categories

    def test_merge_feeds_with_tag_filters(self):
        """Test merge_feeds_into_one_atom with tag filters applied."""
        # Create a tag with filters (we'll need to create a filter model)
        from core.models import Filter

        # Create a simple filter
        filter_obj = Filter.objects.create(
            name="Test Filter",
            keywords="test,example",
            filter_method=Filter.KEYWORD_ONLY,
            operation=Filter.EXCLUDE,
        )

        # Add filter to tag
        self.tag.filters.add(filter_obj)

        # Create feeds with entries
        f1 = self._create_feed_with_entry(translated=False, url_suffix="filter_1")
        f2 = self._create_feed_with_entry(translated=False, url_suffix="filter_2")

        # Test with filters applied
        result = merge_feeds_into_one_atom(self.tag.slug, [f1, f2], feed_type="o")
        self.assertIsNotNone(result)
        self.assertIn("Original Title", result)

    def test_merge_feeds_with_all_empty_entries(self):
        """Test merge_feeds_into_one_atom with all feeds having no entries."""
        # Create feeds with no entries
        feed1 = Feed.objects.create(
            feed_url="https://example.com/rss_empty1.xml", name="Test Feed Empty 1"
        )
        feed1.tags.add(self.tag)

        feed2 = Feed.objects.create(
            feed_url="https://example.com/rss_empty2.xml", name="Test Feed Empty 2"
        )
        feed2.tags.add(self.tag)

        # All feeds have no entries
        result = merge_feeds_into_one_atom(self.tag.slug, [feed1, feed2], feed_type="o")
        self.assertIsNotNone(result)
        self.assertIn("Test Feed Empty 1", result)  # Feed names should be in categories
        self.assertIn("Test Feed Empty 2", result)

    def test_generate_atom_feed_with_exception_during_finalization(self):
        """Test generate_atom_feed exception handling during finalization."""
        feed = Feed.objects.create(
            feed_url="https://example.com/rss_finalization_exception.xml",
            name="Test Feed Finalization Exception",
        )
        feed.tags.add(self.tag)

        # Create an entry
        entry_data = self.base_entry_data.copy()
        entry_data["feed"] = feed
        Entry.objects.create(**entry_data)

        # Mock _finalize_atom_feed to raise an exception
        from unittest.mock import patch

        with patch("core.cache._finalize_atom_feed") as mock_finalize:
            mock_finalize.side_effect = Exception("Finalization error")

            # This should not raise an exception due to try-catch
            result = generate_atom_feed(feed, feed_type="t")
            self.assertIsNone(result)  # Should return None on exception

    def test_generate_atom_feed_with_exception_during_building(self):
        """Test generate_atom_feed exception handling during feed building."""
        feed = Feed.objects.create(
            feed_url="https://example.com/rss_building_exception.xml",
            name="Test Feed Building Exception",
        )
        feed.tags.add(self.tag)

        # Create an entry
        entry_data = self.base_entry_data.copy()
        entry_data["feed"] = feed
        Entry.objects.create(**entry_data)

        # Mock _build_atom_feed to raise an exception
        from unittest.mock import patch

        with patch("core.cache._build_atom_feed") as mock_build:
            mock_build.side_effect = Exception("Building error")

            # This should not raise an exception due to try-catch
            result = generate_atom_feed(feed, feed_type="t")
            self.assertIsNone(result)  # Should return None on exception

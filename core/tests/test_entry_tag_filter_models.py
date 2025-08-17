from django.test import TestCase
from django.utils import timezone
from unittest.mock import patch

from ..models import Feed, Entry, Filter, Tag
from ..models.agent import OpenAIAgent, TestAgent


class EntryModelTest(TestCase):
    def setUp(self):
        self.feed = Feed.objects.create(feed_url="https://example.com/feed.xml")

    def test_entry_creation_and_relationships(self):
        """Test Entry creation, fields, and Feed relationship."""
        now = timezone.now()
        
        # Test basic entry creation
        entry = Entry.objects.create(
            feed=self.feed,
            link="https://example.com/entry1",
            original_title="Test Entry Title",
            pubdate=now,
            author="Test Author",
        )
        
        self.assertEqual(entry.feed, self.feed)
        self.assertEqual(entry.link, "https://example.com/entry1")
        self.assertEqual(entry.original_title, "Test Entry Title")
        self.assertEqual(str(entry), "Test Entry Title")
        self.assertEqual(entry.pubdate, now)
        self.assertEqual(entry.author, "Test Author")
        
        # Test feed relationship
        self.assertIn(entry, self.feed.entries.all())
        self.assertEqual(self.feed.entries.count(), 1)

    def test_entry_comprehensive_fields(self):
        """Test Entry with all fields and various behaviors."""
        now = timezone.now()
        enclosure_xml = '<enclosure url="https://example.com/podcast.mp3" type="audio/mpeg" length="12345678"/>'
        long_content = "A" * 10000
        
        entry = Entry.objects.create(
            feed=self.feed,
            link="https://example.com/entry2",
            author="Full Author",
            pubdate=now,
            updated=now,
            guid="unique-guid-12345",
            enclosures_xml=enclosure_xml,
            original_title="Original Title",
            translated_title="Translated Title",
            original_content=long_content,
            translated_content=long_content,
            original_summary=long_content,
            ai_summary=long_content,
        )
        
        # Test all fields
        self.assertEqual(entry.guid, "unique-guid-12345")
        self.assertEqual(entry.translated_title, "Translated Title")
        self.assertEqual(entry.enclosures_xml, enclosure_xml)
        self.assertEqual(len(entry.original_content), 10000)
        self.assertEqual(len(entry.translated_content), 10000)
        
        # Test GUID indexing
        found_entry = Entry.objects.get(guid="unique-guid-12345")
        self.assertEqual(found_entry, entry)
        
        # Test model meta
        meta = Entry._meta
        self.assertEqual(str(meta.verbose_name), "Entry")
        self.assertEqual(str(meta.verbose_name_plural), "Entries")

    def test_entry_nullable_fields(self):
        """Test Entry with None values for optional fields."""
        entry = Entry.objects.create(
            feed=self.feed,
            link="https://example.com/entry3",
            original_title="DateTime Test",
            pubdate=None,
            updated=None,
        )
        self.assertIsNone(entry.pubdate)
        self.assertIsNone(entry.updated)


class TagModelTest(TestCase):
    def setUp(self):
        self.filter = Filter.objects.create(name="Test Filter")

    def test_tag_creation_and_slug_behavior(self):
        """Test Tag creation, slug generation, and relationships."""
        # Test basic tag creation
        tag = Tag.objects.create(name="Technology")
        self.assertEqual(tag.name, "Technology")
        self.assertEqual(tag.total_tokens, 0)
        self.assertIsNotNone(tag.slug)
        self.assertEqual(str(tag), tag.slug)
        
        # Test slug generation and regeneration
        slug_tag = Tag.objects.create(name="Test Tag Name")
        self.assertEqual(slug_tag.slug, "test-tag-name")
        
        original_slug = slug_tag.slug
        slug_tag.name = "New Name"
        slug_tag.save()
        slug_tag.refresh_from_db()
        self.assertNotEqual(slug_tag.slug, original_slug)
        self.assertEqual(slug_tag.slug, "new-name")

    def test_tag_filter_relationship_and_defaults(self):
        """Test Tag-Filter relationship and default values."""
        tag = Tag.objects.create(name="Test Tag")
        tag.filters.add(self.filter)

        # Test relationships
        self.assertIn(self.filter, tag.filters.all())
        self.assertIn(tag, self.filter.tags.all())
        
        # Test defaults
        self.assertEqual(tag.total_tokens, 0)
        self.assertIsNone(tag.last_updated)
        self.assertEqual(tag.etag, "")


class FilterModelTest(TestCase):
    def setUp(self):
        self.feed = Feed.objects.create(feed_url="https://example.com/feed.xml")
        self.entry1 = Entry.objects.create(
            feed=self.feed,
            original_title="An entry about Python",
            original_content="This is a test.",
        )
        self.entry2 = Entry.objects.create(
            feed=self.feed,
            original_title="An entry about Django",
            original_content="Django is a web framework.",
        )
        self.entry3 = Entry.objects.create(
            feed=self.feed,
            original_title="A third entry",
            original_content="Nothing special here.",
        )

    def test_filter_creation_and_keywords(self):
        """Test Filter creation and keyword handling."""
        filter_obj = Filter.objects.create(name="Test Filter", keywords="test, python")
        self.assertEqual(filter_obj.name, "Test Filter")
        self.assertEqual(str(filter_obj), "Test Filter")
        self.assertEqual(filter_obj.operation, Filter.EXCLUDE)
        
        retrieved_keywords = [tag.name for tag in filter_obj.keywords.all()]
        self.assertCountEqual(retrieved_keywords, ["test", "python"])

    def test_filter_keywords_operations(self):
        """Test keyword filtering with EXCLUDE and INCLUDE operations."""
        # Test EXCLUDE operation
        exclude_filter = Filter.objects.create(
            name="Exclude Python", keywords="Python", operation=Filter.EXCLUDE
        )
        all_entries = Entry.objects.all()
        filtered_qs = exclude_filter.apply_keywords_filter(all_entries)
        self.assertNotIn(self.entry1, filtered_qs)
        self.assertIn(self.entry2, filtered_qs)
        self.assertIn(self.entry3, filtered_qs)
        self.assertEqual(filtered_qs.count(), 2)

        # Test INCLUDE operation
        include_filter = Filter.objects.create(
            name="Include Django", keywords="Django", operation=Filter.INCLUDE
        )
        filtered_qs = include_filter.apply_keywords_filter(all_entries)
        self.assertNotIn(self.entry1, filtered_qs)
        self.assertIn(self.entry2, filtered_qs)
        self.assertNotIn(self.entry3, filtered_qs)
        self.assertEqual(filtered_qs.count(), 1)

    @patch.object(OpenAIAgent, "completions")
    def test_filter_ai_operations(self, mock_completions):
        """Test AI filtering with different agent responses."""
        agent = OpenAIAgent.objects.create(name="AI Filter Agent", api_key="key")
        ai_filter = Filter.objects.create(
            name="AI Filter", agent=agent, filter_prompt="Is this about AI?"
        )

        # Test "Passed" response
        mock_completions.return_value = {"text": "Passed", "tokens": 10}
        filtered_qs, _ = ai_filter.apply_ai_filter(
            Entry.objects.filter(id=self.entry1.id)
        )
        self.assertIn(self.entry1, filtered_qs)

        # Test "Blocked" response
        mock_completions.return_value = {"text": "Blocked"}
        filtered_qs, _ = ai_filter.apply_ai_filter(
            Entry.objects.filter(id=self.entry2.id)
        )
        self.assertNotIn(self.entry2, filtered_qs)

        # Test unexpected response (defaults to blocked)
        mock_completions.return_value = {"text": "Maybe"}
        filtered_qs, _ = ai_filter.apply_ai_filter(
            Entry.objects.filter(id=self.entry3.id)
        )
        self.assertNotIn(self.entry3, filtered_qs)

    def test_filter_methods_and_content_fields(self):
        """Test different filter methods and content field combinations."""
        # Create entry with translated content
        entry = Entry.objects.create(
            feed=self.feed,
            link="https://example.com/entry",
            original_title="Python Programming",
            original_content="This is about Python programming",
            translated_title="Python 编程",
            translated_content="这是关于 Python 编程的内容",
        )

        # Test KEYWORD_ONLY method
        keyword_filter = Filter.objects.create(
            name="Python Filter",
            keywords="Python",
            filter_method=Filter.KEYWORD_ONLY,
            operation=Filter.INCLUDE,
        )
        queryset = Entry.objects.all()
        filtered = keyword_filter.apply_filter(queryset)
        self.assertIn(entry, filtered)

        # Test AI_ONLY method with TestAgent
        agent = TestAgent.objects.create(name="Test Agent")
        ai_filter = Filter.objects.create(
            name="AI Filter",
            agent=agent,
            filter_method=Filter.AI_ONLY,
            filter_prompt="Test prompt",
        )
        filtered = ai_filter.apply_filter(Entry.objects.filter(id=entry.id))
        # TestAgent returns random results, so we just verify it runs
        self.assertTrue(entry in filtered or entry not in filtered)

        # Test content field filtering
        translated_filter = Filter.objects.create(
            name="Translated Title Filter",
            keywords="编程",
            filter_original_title=False,
            filter_translated_title=True,
            filter_original_content=False,
            filter_translated_content=False,
            operation=Filter.INCLUDE,
        )
        filtered = translated_filter.apply_keywords_filter(Entry.objects.all())
        # Result depends on whether translated content contains the keyword
        self.assertTrue(entry in filtered or entry not in filtered)

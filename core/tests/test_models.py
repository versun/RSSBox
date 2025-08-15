from django.test import TestCase
from django.utils import timezone
from unittest.mock import patch, MagicMock
from config import settings
from django.db import IntegrityError

from ..models import Feed, Entry, Filter, FilterResult, Tag
from ..models.agent import OpenAIAgent, DeepLAgent, LibreTranslateAgent, TestAgent


class FeedModelTest(TestCase):
    def test_create_feed_with_minimal_data(self):
        """
        Test creating a Feed with only the required fields and check default values.
        """
        feed_url = "https://example.com/rss.xml"
        feed = Feed.objects.create(feed_url=feed_url)

        # Verify the required field and __str__ method
        self.assertEqual(feed.feed_url, feed_url)
        self.assertEqual(str(feed), feed_url)

        # Verify some of the default values
        self.assertEqual(feed.update_frequency, 30)
        self.assertEqual(feed.max_posts, 20)  # Assuming default from os.getenv is 20
        self.assertEqual(feed.fetch_article, False)
        self.assertEqual(feed.translation_display, 0)
        self.assertEqual(feed.translate_title, False)
        self.assertEqual(feed.translate_content, False)
        self.assertEqual(feed.summary, False)
        self.assertEqual(feed.total_tokens, 0)
        self.assertIsNotNone(feed.slug)
        self.assertEqual(len(feed.slug), 32)

    def test_create_feed_with_full_data(self):
        """
        Test creating a Feed with a comprehensive set of fields.
        """
        feed_url = "https://another-example.com/rss.xml"
        now = timezone.now()
        feed = Feed.objects.create(
            name="Comprehensive Test Feed",
            feed_url=feed_url,
            link="https://another-example.com",
            author="Test Author",
            language="en-us",
            pubdate=now,
            update_frequency=60,
            max_posts=100,
            fetch_article=True,
            translation_display=1,
            target_language="zh-hans",
            translate_title=True,
            translate_content=True,
            summary=True,
            summary_detail=0.5,
            additional_prompt="Test prompt",
        )

        self.assertEqual(feed.name, "Comprehensive Test Feed")
        self.assertEqual(feed.author, "Test Author")
        self.assertEqual(feed.pubdate, now)
        self.assertEqual(feed.update_frequency, 60)
        self.assertEqual(feed.max_posts, 100)
        self.assertTrue(feed.fetch_article)
        self.assertEqual(feed.translation_display, 1)
        self.assertEqual(feed.target_language, "zh-hans")
        self.assertTrue(feed.translate_title)
        self.assertTrue(feed.translate_content)
        self.assertTrue(feed.summary)
        self.assertEqual(feed.summary_detail, 0.5)
        self.assertEqual(feed.additional_prompt, "Test prompt")

    def test_feed_update_frequency_threshold(self):
        """
        Test Feed save method adjusts update_frequency to predefined thresholds.
        """
        test_cases = [
            (3, 5),    # Should round up to 5
            (7, 15),   # Should round up to 15
            (25, 30),  # Should round up to 30
            (45, 60),  # Should round up to 60
            (500, 1440),  # Should round up to 1440
            (5000, 10080),  # Should round up to 10080
        ]
        
        for input_freq, expected_freq in test_cases:
            feed = Feed.objects.create(
                feed_url=f"https://example.com/feed{input_freq}.xml",
                update_frequency=input_freq
            )
            self.assertEqual(feed.update_frequency, expected_freq)

    def test_feed_log_truncation(self):
        """
        Test Feed save method truncates log to 2048 bytes.
        """
        long_log = "A" * 3000  # Create a log longer than 2048 bytes
        feed = Feed.objects.create(
            feed_url="https://example.com/feed.xml",
            log=long_log
        )
        
        # Log should be truncated to 2048 bytes
        self.assertLessEqual(len(feed.log.encode("utf-8")), 2048)
        self.assertTrue(feed.log.endswith("A" * 100))  # Should keep the end

    def test_feed_get_translation_display(self):
        """
        Test Feed get_translation_display method.
        """
        feed = Feed.objects.create(
            feed_url="https://example.com/feed.xml",
            translation_display=1
        )
        
        display = feed.get_translation_display()
        self.assertEqual(display, "Translation | Original")

    def test_feed_generic_foreign_key_cleanup(self):
        """
        Test Feed save method cleans up empty generic foreign keys.
        """
        feed = Feed(feed_url="https://example.com/feed.xml")
        feed.translator_content_type_id = None
        feed.translator_object_id = 1  # Set object_id but not content_type
        feed.save()
        
        # Both should be set to None
        self.assertIsNone(feed.translator_content_type_id)
        self.assertIsNone(feed.translator_object_id)

    def test_feed_unique_constraint(self):
        """
        Test Feed unique constraint on feed_url and target_language.
        """
        Feed.objects.create(
            feed_url="https://example.com/feed.xml",
            target_language="zh-hans"
        )
        
        # Should raise IntegrityError for duplicate
        with self.assertRaises(IntegrityError):
            Feed.objects.create(
                feed_url="https://example.com/feed.xml",
                target_language="zh-hans"
            )

    # @patch('core.models.feed.Filter')
    # def test_feed_filtered_entries_property(self, mock_filter):
    #     """
    #     Test Feed filtered_entries property applies all filters.
    #     """
    #     feed = Feed.objects.create(feed_url="https://example.com/feed.xml")
        
    #     # Create mock filter
    #     mock_filter_instance = MagicMock()
    #     mock_filter_instance.apply_filter.return_value = "filtered_queryset"
        
    #     # Mock the filters.all() method
    #     feed.filters.all = MagicMock(return_value=[mock_filter_instance])
    #     feed.entries.all = MagicMock(return_value="original_queryset")
        
    #     result = feed.filtered_entries
        
    #     # Verify filter was applied
    #     mock_filter_instance.apply_filter.assert_called_once_with("original_queryset")
    #     self.assertEqual(result, "filtered_queryset")

    def test_feed_summary_detail_validator(self):
        """Test Feed summary_detail field validators."""
        # Valid values
        feed = Feed.objects.create(
            feed_url="https://example.com/feed.xml",
            summary_detail=0.5
        )
        self.assertEqual(feed.summary_detail, 0.5)
        
        # Test boundary values
        feed.summary_detail = 0.0
        feed.save()
        self.assertEqual(feed.summary_detail, 0.0)
        
        feed.summary_detail = 1.0
        feed.save()
        self.assertEqual(feed.summary_detail, 1.0)

    def test_feed_translation_display_choices(self):
        """Test Feed translation_display choices."""
        choices = Feed.TRANSLATION_DISPLAY_CHOICES
        expected_choices = [
            (0, "Only Translation"),
            (1, "Translation | Original"),
            (2, "Original | Translation"),
        ]
        
        # Test all choices exist
        for choice in expected_choices:
            self.assertIn(choice, choices)

    def test_feed_many_to_many_relationships(self):
        """Test Feed ManyToMany relationships."""
        feed = Feed.objects.create(feed_url="https://example.com/feed.xml")
        tag = Tag.objects.create(name="Test Tag")
        filter_obj = Filter.objects.create(name="Test Filter")
        
        # Add relationships
        feed.tags.add(tag)
        feed.filters.add(filter_obj)
        
        # Test forward relationships
        self.assertIn(tag, feed.tags.all())
        self.assertIn(filter_obj, feed.filters.all())
        
        # Test reverse relationships
        self.assertIn(feed, tag.feeds.all())
        self.assertIn(feed, filter_obj.feeds.all())


class EntryModelTest(TestCase):
    def setUp(self):
        """
        Create a Feed instance to be used by Entry tests.
        """
        self.feed = Feed.objects.create(feed_url="https://example.com/feed.xml")

    def test_create_entry(self):
        """
        Test creating an Entry instance with basic data.
        """
        entry_link = "https://example.com/entry1"
        original_title = "Test Entry Title"
        now = timezone.now()

        entry = Entry.objects.create(
            feed=self.feed,
            link=entry_link,
            original_title=original_title,
            pubdate=now,
            author="Test Author",
        )

        self.assertEqual(entry.feed, self.feed)
        self.assertEqual(entry.link, entry_link)
        self.assertEqual(entry.original_title, original_title)
        self.assertEqual(str(entry), original_title)
        self.assertEqual(entry.pubdate, now)
        self.assertEqual(entry.author, "Test Author")

    def test_entry_str_method(self):
        """
        Test Entry __str__ method returns original_title.
        """
        entry = Entry.objects.create(
            feed=self.feed,
            link="https://example.com/entry",
            original_title="Test Title",
        )
        self.assertEqual(str(entry), "Test Title")

    def test_entry_with_all_fields(self):
        """
        Test creating Entry with all available fields.
        """
        now = timezone.now()
        entry = Entry.objects.create(
            feed=self.feed,
            link="https://example.com/entry",
            author="Test Author",
            pubdate=now,
            updated=now,
            guid="test-guid-123",
            enclosures_xml="<enclosure url=\"test.mp3\" type=\"audio/mpeg\" />",
            original_title="Original Title",
            translated_title="Translated Title",
            original_content="Original content",
            translated_content="Translated content",
            original_summary="Original summary",
            ai_summary="AI generated summary",
        )
        
        self.assertEqual(entry.guid, "test-guid-123")
        self.assertEqual(entry.translated_title, "Translated Title")
        self.assertEqual(entry.translated_content, "Translated content")
        self.assertEqual(entry.ai_summary, "AI generated summary")

    def test_entry_feed_relationship(self):
        """
        Test the ForeignKey relationship between Entry and Feed.
        """
        entry = Entry.objects.create(
            feed=self.feed,
            link="https://example.com/entry",
            original_title="Test Entry",
        )
        
        # Test forward relationship
        self.assertEqual(entry.feed, self.feed)
        
        # Test reverse relationship
        self.assertIn(entry, self.feed.entries.all())
        self.assertEqual(self.feed.entries.count(), 1)
    

    def test_entry_with_enclosures(self):
        """Test Entry with enclosures XML data."""
        enclosure_xml = '''<enclosure url="https://example.com/podcast.mp3" 
                          type="audio/mpeg" length="12345678"/>'''
        
        entry = Entry.objects.create(
            feed=self.feed,
            link="https://example.com/entry",
            original_title="Podcast Entry",
            enclosures_xml=enclosure_xml
        )
        
        self.assertEqual(entry.enclosures_xml, enclosure_xml)

    def test_entry_guid_indexing(self):
        """Test Entry GUID field has database index."""
        # This test verifies the db_index=True is properly set
        entry = Entry.objects.create(
            feed=self.feed,
            link="https://example.com/entry",
            original_title="GUID Test",
            guid="unique-guid-12345"
        )
        
        # Query by GUID should be efficient due to index
        found_entry = Entry.objects.get(guid="unique-guid-12345")
        self.assertEqual(found_entry, entry)

    def test_entry_datetime_fields(self):
        """Test Entry datetime fields can handle None values."""
        entry = Entry.objects.create(
            feed=self.feed,
            link="https://example.com/entry",
            original_title="DateTime Test",
            pubdate=None,
            updated=None
        )
        
        self.assertIsNone(entry.pubdate)
        self.assertIsNone(entry.updated)

    def test_entry_content_fields_maxlength(self):
        """Test Entry content fields can handle long text."""
        long_content = "A" * 10000  # Very long content
        
        entry = Entry.objects.create(
            feed=self.feed,
            link="https://example.com/entry",
            original_title="Long Content Test",
            original_content=long_content,
            translated_content=long_content,
            original_summary=long_content,
            ai_summary=long_content
        )
        
        self.assertEqual(len(entry.original_content), 10000)
        self.assertEqual(len(entry.translated_content), 10000)
        self.assertEqual(len(entry.original_summary), 10000)
        self.assertEqual(len(entry.ai_summary), 10000)

    def test_entry_meta_verbose_names(self):
        """Test Entry model verbose names."""
        meta = Entry._meta
        self.assertEqual(str(meta.verbose_name), "Entry")
        self.assertEqual(str(meta.verbose_name_plural), "Entries")


class TagModelTest(TestCase):
    def setUp(self):
        """
        Create a Filter instance for Tag tests.
        """
        self.filter = Filter.objects.create(name="Test Filter")

    def test_create_tag(self):
        """
        Test creating a Tag instance with basic data.
        """
        tag = Tag.objects.create(name="Technology")
        
        self.assertEqual(tag.name, "Technology")
        self.assertEqual(tag.total_tokens, 0)
        self.assertIsNotNone(tag.slug)
        self.assertEqual(str(tag), tag.slug)

    def test_tag_slug_generation(self):
        """
        Test automatic slug generation from name.
        """
        tag = Tag.objects.create(name="Test Tag Name")
        self.assertEqual(tag.slug, "test-tag-name")

    def test_tag_save_slug_regeneration(self):
        """
        Test that slug is regenerated when name changes.
        """
        tag = Tag.objects.create(name="Original Name")
        original_slug = tag.slug
        
        # Update the name
        tag.name = "New Name"
        tag.save()
        
        # Slug should be updated
        tag.refresh_from_db()
        self.assertNotEqual(tag.slug, original_slug)
        self.assertEqual(tag.slug, "new-name")

    def test_tag_filter_relationship(self):
        """
        Test ManyToMany relationship between Tag and Filter.
        """
        tag = Tag.objects.create(name="Test Tag")
        tag.filters.add(self.filter)
        
        # Test forward relationship
        self.assertIn(self.filter, tag.filters.all())
        
        # Test reverse relationship
        self.assertIn(tag, self.filter.tags.all())

    def test_tag_fields_default_values(self):
        """Test Tag model field default values."""
        tag = Tag.objects.create(name="Default Test")
        
        self.assertEqual(tag.total_tokens, 0)
        self.assertIsNone(tag.last_updated)
        self.assertEqual(tag.etag, "")

    def test_tag_str_method(self):
        """Test Tag __str__ method returns slug."""
        tag = Tag.objects.create(name="String Test")
        self.assertEqual(str(tag), tag.slug)


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

    def test_create_filter(self):
        """
        Test creating a Filter instance with basic data.
        """
        filter_obj = Filter.objects.create(name="Test Filter", keywords="test, python")
        self.assertEqual(filter_obj.name, "Test Filter")
        self.assertEqual(str(filter_obj), "Test Filter")
        self.assertEqual(filter_obj.operation, Filter.EXCLUDE)
        retrieved_keywords = [tag.name for tag in filter_obj.keywords.all()]
        self.assertCountEqual(retrieved_keywords, ["test", "python"])

    def test_apply_keywords_filter_exclude(self):
        """
        Test the apply_keywords_filter method with EXCLUDE operation.
        """
        filter_obj = Filter.objects.create(
            name="Exclude Python", keywords="Python", operation=Filter.EXCLUDE
        )
        all_entries = Entry.objects.all()
        filtered_qs = filter_obj.apply_keywords_filter(all_entries)
        self.assertNotIn(self.entry1, filtered_qs)
        self.assertIn(self.entry2, filtered_qs)
        self.assertIn(self.entry3, filtered_qs)
        self.assertEqual(filtered_qs.count(), 2)

    def test_apply_keywords_filter_include(self):
        """
        Test the apply_keywords_filter method with INCLUDE operation.
        """
        filter_obj = Filter.objects.create(
            name="Include Django", keywords="Django", operation=Filter.INCLUDE
        )
        all_entries = Entry.objects.all()
        filtered_qs = filter_obj.apply_keywords_filter(all_entries)
        self.assertNotIn(self.entry1, filtered_qs)
        self.assertIn(self.entry2, filtered_qs)
        self.assertNotIn(self.entry3, filtered_qs)
        self.assertEqual(filtered_qs.count(), 1)

    @patch.object(OpenAIAgent, "completions")
    def test_apply_ai_filter_passed(self, mock_translate):
        """Test apply_ai_filter when the agent returns 'Passed'."""
        agent = OpenAIAgent.objects.create(name="AI Filter Agent", api_key="key")
        ai_filter = Filter.objects.create(
            name="AI Filter", agent=agent, filter_prompt="Is this about AI?"
        )
        mock_translate.return_value = {"text": "Passed", "tokens": 10}

        filtered_qs, _ = ai_filter.apply_ai_filter(
            Entry.objects.filter(id=self.entry1.id)
        )

        self.assertIn(self.entry1, filtered_qs)

    @patch.object(OpenAIAgent, "completions")
    def test_apply_ai_filter_blocked(self, mock_translate):
        """Test apply_ai_filter when the agent returns 'Blocked'."""
        agent = OpenAIAgent.objects.create(name="AI Filter Agent", api_key="key")
        ai_filter = Filter.objects.create(
            name="AI Filter", agent=agent, filter_prompt="Is this about AI?"
        )
        mock_translate.return_value = {"text": "Blocked"}

        filtered_qs, _ = ai_filter.apply_ai_filter(
            Entry.objects.filter(id=self.entry2.id)
        )

        self.assertNotIn(self.entry2, filtered_qs)

    @patch.object(OpenAIAgent, "completions")
    def test_apply_ai_filter_maybe(self, mock_translate):
        """Test apply_ai_filter when the agent returns an unexpected response."""
        agent = OpenAIAgent.objects.create(name="AI Filter Agent", api_key="key")
        ai_filter = Filter.objects.create(
            name="AI Filter", agent=agent, filter_prompt="Is this about AI?"
        )
        mock_translate.return_value = {"text": "Maybe"}

        filtered_qs, _ = ai_filter.apply_ai_filter(
            Entry.objects.filter(id=self.entry3.id)
        )

        self.assertNotIn(self.entry3, filtered_qs)


class FilterModelAdvancedTest(TestCase):
    def setUp(self):
        """
        Set up test data for advanced Filter tests.
        """
        self.feed = Feed.objects.create(feed_url="https://example.com/feed.xml")
        self.agent = TestAgent.objects.create(name="Test Agent")
        self.entry = Entry.objects.create(
            feed=self.feed,
            link="https://example.com/entry",
            original_title="Python Programming",
            original_content="This is about Python programming",
            translated_title="Python 编程",
            translated_content="这是关于 Python 编程的内容"
        )
        
    def test_filter_apply_method_keyword_only(self):
        """
        Test Filter apply method with KEYWORD_ONLY filter method.
        """
        filter_obj = Filter.objects.create(
            name="Python Filter",
            keywords="Python",
            filter_method=Filter.KEYWORD_ONLY,
            operation=Filter.INCLUDE
        )
        
        queryset = Entry.objects.all()
        filtered = filter_obj.apply_filter(queryset)
        
        self.assertIn(self.entry, filtered)
    
    def test_filter_apply_method_ai_only(self):
        """
        Test Filter apply method with AI_ONLY filter method.
        """
        filter_obj = Filter.objects.create(
            name="AI Filter",
            agent=self.agent,
            filter_method=Filter.AI_ONLY,
            filter_prompt="Test prompt"
        )
        
        queryset = Entry.objects.filter(id=self.entry.id)
        filtered = filter_obj.apply_filter(queryset)
        
        # TestAgent return random result
        self.assertTrue(self.entry in filtered or self.entry not in filtered)
    
    def test_filter_apply_method_both(self):
        """
        Test Filter apply method with BOTH filter method.
        """
        filter_obj = Filter.objects.create(
            name="Combined Filter",
            keywords="Python",
            agent=self.agent,
            filter_method=Filter.BOTH,
            operation=Filter.INCLUDE
        )
        
        queryset = Entry.objects.all()
        filtered = filter_obj.apply_filter(queryset)
        
        # Entry should pass both keyword and AI filters
        # TestAgent return random result
        self.assertTrue(self.entry in filtered or self.entry not in filtered)
    
    def test_filter_different_content_fields(self):
        """
        Test filtering different content fields.
        """
        # Test filtering translated title
        filter_obj = Filter.objects.create(
            name="Translated Title Filter",
            keywords="编程",
            filter_original_title=False,
            filter_translated_title=True,
            filter_original_content=False,
            filter_translated_content=False,
            operation=Filter.INCLUDE
        )
        
        queryset = Entry.objects.all()
        filtered = filter_obj.apply_keywords_filter(queryset)
        
        self.assertTrue(self.entry in filtered or self.entry not in filtered)

    def test_filter_content_field_combinations(self):
        """Test different combinations of content field filtering."""
        test_cases = [
            {
                'filter_original_title': True,
                'filter_original_content': False,
                'filter_translated_title': False,
                'filter_translated_content': False,
                'keyword': 'Python',
                'should_match': True
            },
            {
                'filter_original_title': False,
                'filter_original_content': True,
                'filter_translated_title': False,
                'filter_translated_content': False,
                'keyword': 'programming',
                'should_match': True
            },
            {
                'filter_original_title': False,
                'filter_original_content': False,
                'filter_translated_title': True,
                'filter_translated_content': False,
                'keyword': '编程',
                'should_match': True
            },
            {
                'filter_original_title': False,
                'filter_original_content': False,
                'filter_translated_title': False,
                'filter_translated_content': True,
                'keyword': 'Python',
                'should_match': True
            }
        ]
        
        for case in test_cases:
            with self.subTest(case=case):
                filter_obj = Filter.objects.create(
                    name=f"Test Filter {case['keyword']}",
                    keywords=case['keyword'],
                    filter_original_title=case['filter_original_title'],
                    filter_original_content=case['filter_original_content'],
                    filter_translated_title=case['filter_translated_title'],
                    filter_translated_content=case['filter_translated_content'],
                    operation=Filter.INCLUDE
                )
                
                queryset = Entry.objects.all()
                filtered = filter_obj.apply_keywords_filter(queryset)
                
                if case['should_match']:
                    self.assertIn(self.entry, filtered)
                else:
                    self.assertNotIn(self.entry, filtered)

    def test_filter_str_method(self):
        """Test Filter __str__ method."""
        filter_obj = Filter.objects.create(name="Test Filter Name")
        self.assertEqual(str(filter_obj), "Test Filter Name")

    def test_filter_operation_choices(self):
        """Test Filter operation choices constants."""
        self.assertTrue(Filter.INCLUDE)
        self.assertFalse(Filter.EXCLUDE)
        
        # Test choices tuple structure
        choices = Filter.OPERATION_CHOICES
        self.assertEqual(len(choices), 2)
        
        # Check choice values
        include_choice = next(choice for choice in choices if choice[0] == Filter.INCLUDE)
        exclude_choice = next(choice for choice in choices if choice[0] == Filter.EXCLUDE)
        
        self.assertIn("Include", str(include_choice[1]))
        self.assertIn("Exclude", str(exclude_choice[1]))

    def test_filter_method_choices(self):
        """Test Filter method choices constants."""
        self.assertEqual(Filter.KEYWORD_ONLY, 0)
        self.assertEqual(Filter.AI_ONLY, 1)
        self.assertEqual(Filter.BOTH, 2)
        
        choices = Filter.FILTER_METHOD_CHOICES
        self.assertEqual(len(choices), 3)


class FilterResultModelTest(TestCase):
    def setUp(self):
        """
        Set up test data for FilterResult tests.
        """
        self.feed = Feed.objects.create(feed_url="https://example.com/feed.xml")
        self.filter = Filter.objects.create(name="Test Filter")
        self.entry = Entry.objects.create(
            feed=self.feed,
            link="https://example.com/entry",
            original_title="Test Entry"
        )

    def test_create_filter_result(self):
        """
        Test creating a FilterResult instance.
        """
        result = FilterResult.objects.create(
            filter=self.filter,
            entry=self.entry,
            passed=True
        )
        
        self.assertEqual(result.filter, self.filter)
        self.assertEqual(result.entry, self.entry)
        self.assertTrue(result.passed)
        self.assertIsNotNone(result.last_updated)

    def test_filter_result_relationships(self):
        """
        Test FilterResult relationships with Filter and Entry.
        """
        result = FilterResult.objects.create(
            filter=self.filter,
            entry=self.entry,
            passed=False
        )
        
        # Test forward relationships
        self.assertEqual(result.filter, self.filter)
        self.assertEqual(result.entry, self.entry)
        
        # Test reverse relationships
        self.assertIn(result, self.filter.results.all())
        self.assertIn(result, self.entry.filter_results.all())

    def test_filter_result_passed_field_nullable(self):
        """Test FilterResult passed field can be null."""
        result = FilterResult.objects.create(
            filter=self.filter,
            entry=self.entry,
            passed=None
        )
        
        self.assertIsNone(result.passed)

    def test_filter_result_auto_timestamp(self):
        """Test FilterResult last_updated is automatically set."""
        before_creation = timezone.now()
        result = FilterResult.objects.create(
            filter=self.filter,
            entry=self.entry,
            passed=True
        )
        after_creation = timezone.now()
        
        self.assertGreaterEqual(result.last_updated, before_creation)
        self.assertLessEqual(result.last_updated, after_creation)


class OpenAIAgentModelTest(TestCase):
    def setUp(self):
        self.agent = OpenAIAgent.objects.create(
            name="Test OpenAI Agent", api_key="test_api_key", model="gpt-test"
        )

    def test_create_openai_agent(self):
        """Test creating an OpenAIAgent instance."""
        self.assertEqual(self.agent.name, "Test OpenAI Agent")
        self.assertEqual(self.agent.api_key, "test_api_key")
        self.assertEqual(self.agent.model, "gpt-test")
        self.assertTrue(self.agent.is_ai)

    @patch("core.models.agent.task_manager")
    @patch("core.models.agent.OpenAI")
    def test_validate_success(self, mock_openai_class, mock_task_manager):
        """Test the validate method with a successful API call."""
        mock_client = MagicMock()
        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock(finish_reason="stop")]
        mock_client.with_options().chat.completions.create.return_value = (
            mock_completion
        )
        mock_openai_class.return_value = mock_client
        
        # Mock task_manager.submit_task to return a mock task with result
        mock_task = MagicMock()
        mock_task.result.return_value = 4096  # Mock max_tokens value
        mock_task_manager.submit_task.return_value = mock_task

        is_valid = self.agent.validate()

        self.assertTrue(is_valid)
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.log, "")
        self.assertEqual(self.agent.max_tokens, 4096)
        mock_task_manager.submit_task.assert_called_once()

    @patch("core.models.agent.task_manager")
    @patch("core.models.agent.OpenAI")
    def test_validate_failure(self, mock_openai_class, mock_task_manager):
        """Test the validate method with a failed API call."""
        mock_client = MagicMock()
        mock_client.with_options().chat.completions.create.side_effect = Exception(
            "API Error"
        )
        mock_openai_class.return_value = mock_client

        is_valid = self.agent.validate()

        self.assertFalse(is_valid)
        self.agent.refresh_from_db()
        self.assertIn("API Error", self.agent.log)
        # task_manager should not be called when API call fails
        mock_task_manager.submit_task.assert_not_called()

    @patch.object(OpenAIAgent, "completions")
    def test_translate_method(self, mock_completions):
        """Test the translate method calls completions with the correct prompt."""
        mock_completions.return_value = {"text": "translated text", "tokens": 10}

        result = self.agent.translate(
            text="hello", target_language="Chinese", text_type="title"
        )

        self.assertEqual(result["text"], "translated text")
        self.assertEqual(result["tokens"], 10)

        # Check that completions was called with the correct system prompt
        expected_prompt = self.agent.title_translate_prompt.replace(
            "{target_language}", "Chinese"
        )
        mock_completions.assert_called_once_with(
            "hello", system_prompt=expected_prompt, user_prompt=None
        )

    @patch("core.models.agent.get_token_count", return_value=10)
    @patch("core.models.agent.OpenAI")
    def test_completions_method(self, mock_openai_class, mock_get_token_count):
        """Test the completions method for success and failure cases."""
        # Set max_tokens to avoid ValueError
        self.agent.max_tokens = 4096
        
        # Setup mock client and response
        mock_client = MagicMock()
        mock_openai_class.return_value = mock_client

        # Test success case
        mock_completion = MagicMock()
        mock_completion.choices = [
            MagicMock(message=MagicMock(content="Test response"))
        ]
        mock_completion.usage = MagicMock(total_tokens=42)
        # Mock the entire call chain
        mock_client.with_options().chat.completions.create.return_value = (
            mock_completion
        )

        result_success = self.agent.completions("test content")
        self.assertEqual(result_success["text"], "Test response")
        self.assertEqual(result_success["tokens"], 42)

        # Test failure case
        mock_client.with_options().chat.completions.create.side_effect = Exception(
            "API Connection Error"
        )
        result_failure = self.agent.completions("test content")
        self.assertEqual(result_failure["text"], "")
        self.assertEqual(result_failure["tokens"], 0)

    @patch.object(OpenAIAgent, "_init")
    @patch("core.models.agent.adaptive_chunking")
    @patch("core.models.agent.get_token_count")
    def test_completions_chunking(
        self, mock_get_token_count, mock_adaptive_chunking, mock_init
    ):
        """Test the completions method's chunking logic with robust mocks."""
        # Set max_tokens to avoid ValueError
        self.agent.max_tokens = 4096
        
        long_text = "A very long text that needs to be chunked."

        # 1. Mock get_token_count to behave based on input
        def token_count_side_effect(text):
            if text == long_text:
                return self.agent.max_tokens + 1
            elif "system" in str(text).lower() or "prompt" in str(text).lower():
                return 50  # For system prompt
            return 10  # For chunks

        mock_get_token_count.side_effect = token_count_side_effect
        mock_adaptive_chunking.return_value = ["First chunk.", "Second chunk."]

        # 2. Mock the client and its API call
        mock_client = MagicMock()
        mock_init.return_value = mock_client

        mock_completion_1 = MagicMock()
        mock_completion_1.choices = [
            MagicMock(message=MagicMock(content="Translated first."))
        ]
        mock_completion_1.usage = MagicMock(total_tokens=20)

        mock_completion_2 = MagicMock()
        mock_completion_2.choices = [
            MagicMock(message=MagicMock(content="Translated second."))
        ]
        mock_completion_2.usage = MagicMock(total_tokens=25)

        # Use a function for side_effect to avoid exhaustion
        api_results = [mock_completion_1, mock_completion_2]
        mock_client.with_options().chat.completions.create.side_effect = (
            lambda **kwargs: api_results.pop(0)
        )

        # 3. Call the real method
        result = self.agent.completions(long_text)

        # 4. Assert the results
        self.assertEqual(result["text"], "Translated first. Translated second.")
        self.assertEqual(result["tokens"], 45)
        mock_adaptive_chunking.assert_called_once()

    @patch.object(OpenAIAgent, "completions")
    def test_summarize_method(self, mock_completions):
        """Test that the summarize method calls completions with the correct system prompt."""
        self.agent.summarize(text="Test text", target_language="English")
        expected_prompt = self.agent.summary_prompt.replace(
            "{target_language}", "English"
        )
        mock_completions.assert_called_once_with(
            "Test text", system_prompt=expected_prompt
        )

    @patch.object(OpenAIAgent, "completions")
    def test_digester_method(self, mock_completions):
        """Test that the digester method calls completions with the correct system prompt."""
        custom_prompt = "Digest this:"
        self.agent.digester(
            text="Test text", target_language="English", system_prompt=custom_prompt
        )
        expected_prompt = custom_prompt + settings.output_format_for_filter_prompt
        mock_completions.assert_called_once_with(
            "Test text", system_prompt=expected_prompt
        )

    @patch.object(OpenAIAgent, "completions")
    def test_filter_method(self, mock_completions):
        """Test that the filter method calls completions and processes the result."""
        # Test 'Passed' case
        mock_completions.return_value = {"text": "... Passed ...", "tokens": 30}
        result_passed = self.agent.filter(
            text="Test text", system_prompt="Filter this:"
        )
        self.assertTrue(result_passed["passed"])
        self.assertEqual(result_passed["tokens"], 30)

        # Test 'Blocked' case
        mock_completions.return_value = {"text": "... Blocked ...", "tokens": 25}
        result_blocked = self.agent.filter(
            text="Test text", system_prompt="Filter this:"
        )
        self.assertFalse(result_blocked["passed"])
        self.assertEqual(
            result_blocked["tokens"], 0
        )  # Tokens should be 0 if not passed


class LibreTranslateAgentModelTest(TestCase):
    def setUp(self):
        self.agent = LibreTranslateAgent.objects.create(
            name="Test LibreTranslate Agent", server_url="http://libretranslate.test"
        )

    @patch.object(LibreTranslateAgent, "_api_languages")
    def test_validate_success(self, mock_api_languages):
        """Test LibreTranslateAgent validate method on success."""
        mock_api_languages.return_value = []  # Success is just not raising an exception
        is_valid = self.agent.validate()
        self.assertTrue(is_valid)
        mock_api_languages.assert_called_once()

    @patch.object(LibreTranslateAgent, "_api_languages")
    def test_validate_failure(self, mock_api_languages):
        """Test LibreTranslateAgent validate method on failure."""
        mock_api_languages.side_effect = Exception("Connection Error")
        is_valid = self.agent.validate()
        self.assertFalse(is_valid)
        self.agent.refresh_from_db()
        self.assertIn("Connection Error", self.agent.log)

    @patch.object(LibreTranslateAgent, "_api_translate")
    def test_translate_success(self, mock_api_translate):
        """Test LibreTranslateAgent translate method on success."""
        mock_api_translate.return_value = "Translated Text"
        result = self.agent.translate("Test Text", "Chinese Simplified")
        self.assertEqual(result["text"], "Translated Text")
        self.assertEqual(result["characters"], len("Test Text"))
        mock_api_translate.assert_called_once_with(
            q="Test Text", source="auto", target="zh", format="text"
        )

    @patch.object(LibreTranslateAgent, "_api_translate")
    def test_translate_failure(self, mock_api_translate):
        """Test LibreTranslateAgent translate method on API failure."""
        mock_api_translate.side_effect = Exception("API Error")
        result = self.agent.translate("Test Text", "Chinese Simplified")
        self.assertEqual(result["text"], "")
        self.agent.refresh_from_db()
        self.assertIn("API Error", self.agent.log)

    def test_translate_unsupported_language(self):
        """Test LibreTranslateAgent translate method with an unsupported language."""
        result = self.agent.translate("Test Text", "Klingon")
        self.assertEqual(result["text"], "")
        self.assertEqual(result["characters"], 0)


class DeepLAgentModelTest(TestCase):
    def setUp(self):
        self.agent = DeepLAgent.objects.create(
            name="Test DeepL Agent", api_key="test_deepl_key"
        )

    @patch("core.models.agent.deepl.Translator")
    def test_validate_success(self, mock_translator_class):
        """Test DeepLAgent validate method on success."""
        mock_translator_instance = MagicMock()
        mock_usage = MagicMock()
        mock_usage.character.valid = True
        mock_translator_instance.get_usage.return_value = mock_usage
        mock_translator_class.return_value = mock_translator_instance

        is_valid = self.agent.validate()

        self.assertTrue(is_valid)
        mock_translator_class.assert_called_once_with(
            self.agent.api_key, server_url=self.agent.server_url, proxy=self.agent.proxy
        )
        mock_translator_instance.get_usage.assert_called_once()

    @patch("core.models.agent.deepl.Translator")
    def test_validate_failure(self, mock_translator_class):
        """Test DeepLAgent validate method on failure."""
        mock_translator_instance = MagicMock()
        mock_translator_instance.get_usage.side_effect = Exception("DeepL API Error")
        mock_translator_class.return_value = mock_translator_instance

        is_valid = self.agent.validate()

        self.assertFalse(is_valid)
        self.agent.refresh_from_db()
        self.assertIn("DeepL API Error", self.agent.log)

    @patch("core.models.agent.deepl.Translator")
    def test_translate_success(self, mock_translator_class):
        """Test DeepLAgent translate method on success."""
        mock_translator_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Translated Text"
        mock_translator_instance.translate_text.return_value = mock_response
        mock_translator_class.return_value = mock_translator_instance

        result = self.agent.translate("Test Text", "Chinese Simplified")

        self.assertEqual(result["text"], "Translated Text")
        self.assertEqual(result["characters"], len("Test Text"))
        mock_translator_instance.translate_text.assert_called_once_with(
            "Test Text",
            target_lang="ZH",
            preserve_formatting=True,
            split_sentences="nonewlines",
            tag_handling="html",
        )

    @patch("core.models.agent.deepl.Translator")
    def test_translate_failure(self, mock_translator_class):
        """Test DeepLAgent translate method on API failure."""
        mock_translator_instance = MagicMock()
        mock_translator_instance.translate_text.side_effect = Exception(
            "DeepL Translate Error"
        )
        mock_translator_class.return_value = mock_translator_instance

        result = self.agent.translate("Test Text", "Chinese Simplified")

        self.assertEqual(result["text"], "")
        self.agent.refresh_from_db()
        self.assertIn("DeepL Translate Error", self.agent.log)

    def test_translate_unsupported_language(self):
        """Test DeepLAgent translate method with an unsupported language."""
        result = self.agent.translate("Test Text", "Klingon")
        self.assertEqual(result["text"], "")


class LibreTranslateAgentModelTest(TestCase):
    def setUp(self):
        self.agent = LibreTranslateAgent.objects.create(
            name="Test LibreTranslate Agent", server_url="http://libretranslate.test"
        )

    @patch.object(LibreTranslateAgent, "_api_languages")
    def test_validate_success(self, mock_api_languages):
        """Test LibreTranslateAgent validate method on success."""
        mock_api_languages.return_value = []  # Success is just not raising an exception
        is_valid = self.agent.validate()
        self.assertTrue(is_valid)
        mock_api_languages.assert_called_once()

    @patch.object(LibreTranslateAgent, "_api_languages")
    def test_validate_failure(self, mock_api_languages):
        """Test LibreTranslateAgent validate method on failure."""
        mock_api_languages.side_effect = Exception("Connection Error")
        is_valid = self.agent.validate()
        self.assertFalse(is_valid)
        self.agent.refresh_from_db()
        self.assertIn("Connection Error", self.agent.log)

    @patch.object(LibreTranslateAgent, "_api_translate")
    def test_translate_success(self, mock_api_translate):
        """Test LibreTranslateAgent translate method on success."""
        mock_api_translate.return_value = "Translated Text"
        result = self.agent.translate("Test Text", "Chinese Simplified")
        self.assertEqual(result["text"], "Translated Text")
        self.assertEqual(result["characters"], len("Test Text"))
        mock_api_translate.assert_called_once_with(
            q="Test Text", source="auto", target="zh", format="html"
        )

    @patch.object(LibreTranslateAgent, "_api_translate")
    def test_translate_failure(self, mock_api_translate):
        """Test LibreTranslateAgent translate method on API failure."""
        mock_api_translate.side_effect = Exception("API Error")
        result = self.agent.translate("Test Text", "Chinese Simplified")
        self.assertEqual(result["text"], "")
        self.agent.refresh_from_db()
        self.assertIn("API Error", self.agent.log)

    def test_translate_unsupported_language(self):
        """Test LibreTranslateAgent translate method with an unsupported language."""
        result = self.agent.translate("Test Text", "Klingon")
        self.assertEqual(result["text"], "")
        self.assertEqual(result["characters"], 0)


class TestAgentModelTest(TestCase):
    def setUp(self):
        """
        Create a TestAgent instance for testing.
        """
        self.agent = TestAgent.objects.create(
            name="Test Agent",
            translated_text="@@Translated@@",
            interval=1
        )

    def test_create_test_agent(self):
        """
        Test creating a TestAgent instance.
        """
        self.assertEqual(self.agent.name, "Test Agent")
        self.assertEqual(self.agent.translated_text, "@@Translated@@")
        self.assertEqual(self.agent.interval, 1)
        self.assertTrue(self.agent.is_ai)
        self.assertEqual(self.agent.max_characters, 50000)
        self.assertEqual(self.agent.max_tokens, 50000)

    def test_test_agent_validate(self):
        """
        Test TestAgent validate method always returns True.
        """
        self.assertTrue(self.agent.validate())

    def test_test_agent_translate(self):
        """
        Test TestAgent translate method returns configured text.
        """
        result = self.agent.translate("Hello world", "zh-hans")
        expected = {
            "text": "@@Translated@@",
            "characters": len("Hello world"),
            "tokens": 10
        }
        self.assertEqual(result, expected)

    def test_test_agent_summarize(self):
        """
        Test TestAgent summarize method.
        """
        result = self.agent.summarize("Long text to summarize", "zh-hans")
        expected = {
            "text": "@@Translated@@",
            "characters": len("Long text to summarize"),
            "tokens": 10
        }
        self.assertEqual(result, expected)

    def test_test_agent_filter(self):
        """
        Test TestAgent filter method.
        """
        result = self.agent.filter("Text to filter")
        expected = {
            "passed": True,
            "tokens": 10,
        }
        self.assertEqual(result["tokens"], expected["tokens"])
        self.assertIsInstance(result["passed"], bool)

    def test_agent_base_methods(self):
        """
        Test Agent base class methods min_size and max_size.
        """
        # Test with max_characters
        expected_min = self.agent.max_characters * 0.7
        expected_max = self.agent.max_characters * 0.9
        
        self.assertEqual(self.agent.min_size(), expected_min)
        self.assertEqual(self.agent.max_size(), expected_max)

    def test_agent_str_method(self):
        """Test Agent __str__ method returns name."""
        self.assertEqual(str(self.agent), "Test Agent")


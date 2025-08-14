from django.test import TestCase
from django.utils import timezone
from unittest.mock import patch, Mock
import json

from ..models import Feed, Entry, Filter, FilterResult
from ..models.agent import TestAgent, OpenAIAgent


class FilterExtendedTestCase(TestCase):
    def setUp(self):
        self.feed = Feed.objects.create(
            name="Test Feed", feed_url="https://example.com/feed.xml"
        )
        self.agent = TestAgent.objects.create(name="Test Agent")

        # Create test entries
        self.entry1 = Entry.objects.create(
            feed=self.feed,
            original_title="Python Programming Tutorial",
            original_content="<p>Learn Python programming basics</p>",
            translated_title="Python编程教程",
            translated_content="<p>学习Python编程基础</p>",
        )
        self.entry2 = Entry.objects.create(
            feed=self.feed,
            original_title="JavaScript Guide",
            original_content="<p>JavaScript fundamentals</p>",
            translated_title="JavaScript指南",
            translated_content="<p>JavaScript基础知识</p>",
        )

    def test_apply_keywords_filter_no_keywords(self):
        """Test apply_keywords_filter when no keywords are set."""
        filter_obj = Filter.objects.create(
            name="No Keywords Filter", operation=Filter.INCLUDE
        )

        queryset = Entry.objects.all()
        result = filter_obj.apply_keywords_filter(queryset)

        # Should return empty queryset for INCLUDE operation with no keywords
        self.assertEqual(result.count(), 0)

    def test_apply_keywords_filter_no_keywords_exclude(self):
        """Test apply_keywords_filter with EXCLUDE operation and no keywords."""
        filter_obj = Filter.objects.create(
            name="No Keywords Exclude Filter", operation=Filter.EXCLUDE
        )

        queryset = Entry.objects.all()
        result = filter_obj.apply_keywords_filter(queryset)

        # Should return all entries for EXCLUDE operation with no keywords
        self.assertEqual(result.count(), 2)

    def test_apply_keywords_filter_translated_title(self):
        """Test apply_keywords_filter with translated title filtering."""
        filter_obj = Filter.objects.create(
            name="Translated Title Filter",
            operation=Filter.INCLUDE,
            filter_translated_title=True,
        )
        filter_obj.keywords.add("Python")

        queryset = Entry.objects.all()
        result = filter_obj.apply_keywords_filter(queryset)

        # Should find entry with "Python" in translated title
        self.assertEqual(result.count(), 1)
        self.assertEqual(result.first(), self.entry1)

    def test_apply_keywords_filter_translated_content(self):
        """Test apply_keywords_filter with translated content filtering."""
        filter_obj = Filter.objects.create(
            name="Translated Content Filter",
            operation=Filter.INCLUDE,
            filter_translated_content=True,
        )
        filter_obj.keywords.add("基础")

        queryset = Entry.objects.all()
        result = filter_obj.apply_keywords_filter(queryset)

        # Should find entries with "基础" in translated content
        self.assertEqual(result.count(), 2)

    def test_apply_ai_filter_with_existing_result(self):
        """Test apply_ai_filter when FilterResult already exists."""
        filter_obj = Filter.objects.create(
            name="AI Filter",
            filter_method=Filter.AI_ONLY,
            agent=self.agent,
            filter_prompt="Test prompt",
            filter_original_title=True,
        )

        # Create existing filter result
        FilterResult.objects.create(filter=filter_obj, entry=self.entry1, passed=True)

        queryset = Entry.objects.filter(id=self.entry1.id)
        result_queryset, tokens = filter_obj.apply_ai_filter(queryset)

        # Should use existing result
        self.assertEqual(result_queryset.count(), 1)
        self.assertEqual(tokens, 0)  # No new tokens used

    def test_apply_ai_filter_needs_reevaluation(self):
        """Test apply_ai_filter when entry needs re-evaluation."""
        filter_obj = Filter.objects.create(
            name="AI Filter",
            filter_method=Filter.AI_ONLY,
            agent=self.agent,
            filter_prompt="Test prompt",
            filter_original_title=True,
        )

        # Create old filter result
        old_time = timezone.now() - timezone.timedelta(days=1)
        FilterResult.objects.create(
            filter=filter_obj, entry=self.entry1, passed=False, last_updated=old_time
        )

        # Update entry timestamp
        self.entry1.updated = timezone.now()
        self.entry1.save()

        with patch.object(
            self.agent, "filter", return_value={"passed": True, "tokens": 10}
        ):
            queryset = Entry.objects.filter(id=self.entry1.id)
            result_queryset, tokens = filter_obj.apply_ai_filter(queryset)

        # Should re-evaluate and pass
        self.assertEqual(result_queryset.count(), 1)
        self.assertEqual(tokens, 10)

    def test_apply_ai_filter_with_multiple_fields(self):
        """Test apply_ai_filter with multiple field filters enabled."""
        filter_obj = Filter.objects.create(
            name="Multi Field AI Filter",
            filter_method=Filter.AI_ONLY,
            agent=self.agent,
            filter_prompt="Test prompt",
            filter_original_title=True,
            filter_original_content=True,
            filter_translated_title=True,
            filter_translated_content=True,
        )

        with patch.object(
            self.agent, "filter", return_value={"passed": True, "tokens": 15}
        ) as mock_filter:
            queryset = Entry.objects.filter(id=self.entry1.id)
            result_queryset, tokens = filter_obj.apply_ai_filter(queryset)

            # Verify all fields were included in the filter call
            call_args = mock_filter.call_args
            text_data = json.loads(call_args[1]["text"])

            self.assertIn("original_title", text_data)
            self.assertIn("original_content", text_data)
            self.assertIn("translated_title", text_data)
            self.assertIn("translated_content", text_data)

    def test_apply_filter_keyword_only(self):
        """Test apply_filter with KEYWORD_ONLY method."""
        filter_obj = Filter.objects.create(
            name="Keyword Only Filter",
            filter_method=Filter.KEYWORD_ONLY,
            operation=Filter.INCLUDE,
            filter_original_title=True,
        )
        filter_obj.keywords.add("Python")

        queryset = Entry.objects.all()
        result = filter_obj.apply_filter(queryset)

        self.assertEqual(result.count(), 1)
        self.assertEqual(result.first(), self.entry1)

    def test_apply_filter_ai_only(self):
        """Test apply_filter with AI_ONLY method."""
        filter_obj = Filter.objects.create(
            name="AI Only Filter",
            filter_method=Filter.AI_ONLY,
            agent=self.agent,
            filter_prompt="Test prompt",
            filter_original_title=True,
        )

        self.entry2.delete()

        with patch.object(
            self.agent, "filter", return_value={"passed": True, "tokens": 20}
        ) as mock_filter:
            queryset = Entry.objects.all()
            self.assertEqual(queryset.count(), 1)
            result = filter_obj.apply_filter(queryset)
            mock_filter.assert_called_once()

        # Should update total_tokens
        filter_obj.refresh_from_db()
        self.assertEqual(filter_obj.total_tokens, 20)

    def test_apply_filter_both_methods(self):
        """Test apply_filter with BOTH keyword and AI methods."""
        filter_obj = Filter.objects.create(
            name="Both Methods Filter",
            filter_method=Filter.BOTH,
            operation=Filter.INCLUDE,
            agent=self.agent,
            filter_prompt="Test prompt",
            filter_original_title=True,
        )
        filter_obj.keywords.add("Python")

        with patch.object(
            self.agent, "filter", return_value={"passed": True, "tokens": 25}
        ):
            queryset = Entry.objects.all()
            result = filter_obj.apply_filter(queryset)

        # Should apply both keyword and AI filtering
        filter_obj.refresh_from_db()
        self.assertEqual(filter_obj.total_tokens, 25)

    def test_needs_re_evaluation_never_evaluated(self):
        """Test needs_re_evaluation when result was never evaluated."""
        filter_obj = Filter.objects.create(name="Test Filter")

        result = FilterResult.objects.create(
            filter=filter_obj,
            entry=self.entry1,
            passed=None,  # Never evaluated
        )

        needs_re_eval = filter_obj.needs_re_evaluation(result, self.entry1)
        self.assertTrue(needs_re_eval)

    def test_needs_re_evaluation_entry_updated(self):
        """Test needs_re_evaluation when entry was updated after evaluation."""
        filter_obj = Filter.objects.create(name="Test Filter")

        old_time = timezone.now() - timezone.timedelta(hours=1)
        result = FilterResult.objects.create(
            filter=filter_obj, entry=self.entry1, passed=True, last_updated=old_time
        )

        # Update entry
        self.entry1.updated = timezone.now()
        self.entry1.save()

        needs_re_eval = filter_obj.needs_re_evaluation(result, self.entry1)
        self.assertTrue(needs_re_eval)

    def test_save_new_filter(self):
        """Test save method for new filter."""
        filter_obj = Filter(name="New Filter")

        # Should save without issues
        filter_obj.save()
        self.assertIsNotNone(filter_obj.pk)

    def test_save_existing_filter_no_changes(self):
        """Test save method for existing filter with no changes."""
        filter_obj = Filter.objects.create(name="Existing Filter")

        # Save again without changes
        filter_obj.save()
        # Should not raise any errors

    def test_save_existing_filter_keywords_changed(self):
        """Test save method when keywords are changed."""
        filter_obj = Filter.objects.create(
            name="Keywords Filter", filter_method=Filter.AI_ONLY, agent=self.agent
        )
        filter_obj.keywords.add("original")

        # Create some filter results
        FilterResult.objects.create(filter=filter_obj, entry=self.entry1, passed=True)

        with patch.object(filter_obj, "clear_ai_filter_cache_results") as mock_clear:
            # Change keywords
            filter_obj.keywords.add("new_keyword")
            filter_obj.save()

            # Should not clear cache for keyword changes in AI_ONLY mode
            mock_clear.assert_not_called()

    def test_save_existing_filter_ai_fields_changed(self):
        """Test save method when AI-related fields are changed."""
        filter_obj = Filter.objects.create(
            name="AI Filter",
            filter_method=Filter.AI_ONLY,
            agent=self.agent,
            filter_prompt="Original prompt",
        )

        # Create some filter results
        FilterResult.objects.create(filter=filter_obj, entry=self.entry1, passed=True)

        with patch.object(filter_obj, "clear_ai_filter_cache_results") as mock_clear:
            # Change AI-related field
            filter_obj.filter_prompt = "New prompt"
            filter_obj.save()

            # Should clear cache for AI field changes
            mock_clear.assert_called_once()

    def test_save_existing_filter_agent_changed(self):
        """Test save method when agent is changed."""
        new_agent = OpenAIAgent.objects.create(name="New Agent", api_key="test-key")

        filter_obj = Filter.objects.create(
            name="Agent Filter", filter_method=Filter.BOTH, agent=self.agent
        )

        with patch.object(filter_obj, "clear_ai_filter_cache_results") as mock_clear:
            # Change agent
            filter_obj.agent = new_agent
            filter_obj.save()

            # Should clear cache when agent changes
            mock_clear.assert_called_once()

    def test_clear_ai_filter_cache_results(self):
        """Test clear_ai_filter_cache_results method."""
        filter_obj = Filter.objects.create(name="Cache Filter")

        # Create multiple filter results
        FilterResult.objects.create(filter=filter_obj, entry=self.entry1, passed=True)
        FilterResult.objects.create(filter=filter_obj, entry=self.entry2, passed=False)

        # Verify results exist
        self.assertEqual(FilterResult.objects.filter(filter=filter_obj).count(), 2)

        # Clear cache
        filter_obj.clear_ai_filter_cache_results()

        # Verify results are deleted
        self.assertEqual(FilterResult.objects.filter(filter=filter_obj).count(), 0)

    def test_filter_method_keyword_only_no_ai(self):
        """Test filter with KEYWORD_ONLY method doesn't use AI even if agent is set."""
        filter_obj = Filter.objects.create(
            name="Keyword Only No AI",
            filter_method=Filter.KEYWORD_ONLY,
            agent=self.agent,  # Agent is set but shouldn't be used
            operation=Filter.INCLUDE,
            filter_original_title=True,
        )
        filter_obj.keywords.add("Python")

        with patch.object(self.agent, "filter") as mock_filter:
            queryset = Entry.objects.all()
            result = filter_obj.apply_filter(queryset)

            # AI filter should not be called
            mock_filter.assert_not_called()
            self.assertEqual(result.count(), 1)

    def test_apply_ai_filter_no_agent(self):
        """Test apply_ai_filter when no agent is set."""
        filter_obj = Filter.objects.create(
            name="No Agent Filter", filter_method=Filter.AI_ONLY, agent=None
        )

        queryset = Entry.objects.all()
        result_queryset, tokens = filter_obj.apply_ai_filter(queryset)

        # Should return empty queryset and 0 tokens
        self.assertEqual(result_queryset.count(), 0)
        self.assertEqual(tokens, 0)

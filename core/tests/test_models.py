from django.test import TestCase
from django.utils import timezone
from unittest.mock import patch, MagicMock
from config import settings

from ..models import Feed, Entry, Filter, Tag
from ..models.agent import OpenAIAgent, DeepLAgent, LibreTranslateAgent


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
        self.assertEqual(self.feed.entries.count(), 1)


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

    @patch("core.models.agent.OpenAI")
    def test_validate_success(self, mock_openai_class):
        """Test the validate method with a successful API call."""
        mock_client = MagicMock()
        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock(finish_reason="stop")]
        mock_client.with_options().chat.completions.create.return_value = (
            mock_completion
        )
        mock_openai_class.return_value = mock_client

        is_valid = self.agent.validate()

        self.assertTrue(is_valid)
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.log, "")

    @patch("core.models.agent.OpenAI")
    def test_validate_failure(self, mock_openai_class):
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

        result_success = self.agent.completions("test prompt")
        self.assertEqual(result_success["text"], "Test response")
        self.assertEqual(result_success["tokens"], 42)

        # Test failure case
        mock_client.with_options().chat.completions.create.side_effect = Exception(
            "API Connection Error"
        )
        result_failure = self.agent.completions("test prompt")
        self.assertIn("", result_failure["text"])
        self.assertEqual(result_failure["tokens"], 0)

    @patch.object(OpenAIAgent, "_init")
    @patch("core.models.agent.adaptive_chunking")
    @patch("core.models.agent.get_token_count")
    def test_completions_chunking(
        self, mock_get_token_count, mock_adaptive_chunking, mock_init
    ):
        """Test the completions method's chunking logic with robust mocks."""
        long_text = "A very long text that needs to be chunked."

        # 1. Mock get_token_count to behave based on input
        def token_count_side_effect(text):
            if text == long_text:
                return self.agent.max_tokens + 1
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
            "Test text", system_prompt=expected_prompt, max_tokens=None
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
            "Test text", system_prompt=expected_prompt, max_tokens=None
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

from django.test import TestCase
from django.core.cache import cache
from unittest.mock import patch, MagicMock
import datetime

from ..models.agent import (
    Agent,
    OpenAIAgent,
    DeepLAgent,
    LibreTranslateAgent,
    TestAgent,
)


class OpenAIAgentTest(TestCase):
    def setUp(self):
        self.agent = OpenAIAgent.objects.create(
            name="Test OpenAI Agent",
            api_key="test_api_key",
            model="gpt-test",
            rate_limit_rpm=60,
            max_tokens=1000,
        )
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_openai_agent_creation_and_properties(self):
        """Test OpenAI agent creation and basic properties."""
        self.assertEqual(self.agent.name, "Test OpenAI Agent")
        self.assertEqual(self.agent.api_key, "test_api_key")
        self.assertEqual(self.agent.model, "gpt-test")
        self.assertEqual(self.agent.rate_limit_rpm, 60)
        self.assertEqual(self.agent.max_tokens, 1000)
        self.assertEqual(str(self.agent), "Test OpenAI Agent")

    @patch("core.models.agent.time.sleep")
    def test_openai_agent_rate_limiting(self, mock_sleep):
        """Test OpenAI agent rate limiting functionality."""
        # Test no rate limit
        self.agent.rate_limit_rpm = 0
        self.agent._wait_for_rate_limit()
        mock_sleep.assert_not_called()

        # Test cache increment
        cache.clear()
        self.agent.rate_limit_rpm = 60
        current_minute = datetime.datetime.now().strftime("%Y%m%d%H%M")
        cache_key = f"openai_rate_limit_{self.agent.id}_{current_minute}"

        self.agent._wait_for_rate_limit()
        self.assertEqual(cache.get(cache_key), 1)

        self.agent._wait_for_rate_limit()
        self.assertEqual(cache.get(cache_key), 2)


class DeepLAgentTest(TestCase):
    def setUp(self):
        self.agent = DeepLAgent.objects.create(
            name="Test DeepL Agent", api_key="test_deepl_key"
        )

    def test_deepl_agent_properties(self):
        """Test DeepL agent properties and defaults."""
        self.assertEqual(self.agent.name, "Test DeepL Agent")
        self.assertEqual(self.agent.api_key, "test_deepl_key")
        self.assertEqual(str(self.agent), "Test DeepL Agent")

        # Test language code mapping exists
        self.assertTrue(hasattr(self.agent, "language_code_map"))
        self.assertIsInstance(self.agent.language_code_map, dict)


class LibreTranslateAgentTest(TestCase):
    def setUp(self):
        self.agent = LibreTranslateAgent.objects.create(
            name="Test LibreTranslate Agent", server_url="http://libretranslate.test"
        )

    def test_libretranslate_agent_properties(self):
        """Test LibreTranslate agent properties."""
        self.assertEqual(self.agent.name, "Test LibreTranslate Agent")
        self.assertEqual(self.agent.server_url, "http://libretranslate.test")
        self.assertEqual(str(self.agent), "Test LibreTranslate Agent")


class TestAgentTest(TestCase):
    def setUp(self):
        self.agent = TestAgent.objects.create(name="Test Agent")

    def test_test_agent_operations(self):
        """Test TestAgent validation, translation, and completions."""
        # Test validation
        result = self.agent.validate()
        self.assertTrue(result)  # TestAgent.validate() returns boolean

        # Test translation (TestAgent returns fixed test text)
        result = self.agent.translate("Hello", "es")
        self.assertEqual(result["text"], "@@Translated Text@@")
        self.assertEqual(result["characters"], 5)  # Length of "Hello"

        # TestAgent doesn't have completions method, only translation

        # Test properties
        self.assertEqual(self.agent.name, "Test Agent")
        self.assertEqual(str(self.agent), "Test Agent")


class AgentBaseClassTest(TestCase):
    """Test Agent abstract base class methods and field validation."""

    def test_agent_field_validation_and_boundaries(self):
        """Test Agent model field validation and edge cases."""
        # Test OpenAI agent with various max_tokens values
        agent = OpenAIAgent.objects.create(
            name="Boundary Test Agent", api_key="test_key", max_tokens=1000
        )
        self.assertEqual(agent.max_tokens, 1000)

        # Test boundary values
        for tokens in [1, 32768]:  # Min and max typical values
            agent.max_tokens = tokens
            agent.save()
            self.assertEqual(agent.max_tokens, tokens)

    def test_agent_string_representation(self):
        """Test string representation of different agent types."""
        agents = [
            OpenAIAgent.objects.create(
                name="OpenAI Test", api_key="key", max_tokens=1000
            ),
            DeepLAgent.objects.create(name="DeepL Test", api_key="key"),
            LibreTranslateAgent.objects.create(
                name="LibreTranslate Test", server_url="http://test"
            ),
            TestAgent.objects.create(name="Test Agent"),
        ]

        for agent in agents:
            self.assertEqual(str(agent), agent.name)

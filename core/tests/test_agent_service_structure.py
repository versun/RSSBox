from unittest.mock import patch

from django.test import TestCase

from core.models.agent import (
    DeepLAgent,
    LibreTranslateAgent,
    OpenAIAgent,
    TestAgent,
)


class AgentServiceStructureTests(TestCase):
    def test_agent_service_modules_exist(self):
        from core.services.agent import deepl, libretranslate, openai, test_agent

        self.assertTrue(hasattr(openai, "openai_validate"))
        self.assertTrue(hasattr(openai, "openai_completions"))
        self.assertTrue(hasattr(openai, "openai_detect_model_limit"))
        self.assertTrue(hasattr(openai, "openai_wait_for_rate_limit"))
        self.assertTrue(hasattr(deepl, "deepl_validate"))
        self.assertTrue(hasattr(deepl, "deepl_translate"))
        self.assertTrue(hasattr(libretranslate, "libretranslate_validate"))
        self.assertTrue(hasattr(libretranslate, "libretranslate_translate"))
        self.assertTrue(hasattr(test_agent, "testagent_translate"))

    @patch("core.models.agent.openai_validate", return_value=True)
    def test_openai_validate_delegates_to_service(self, mock_service):
        agent = OpenAIAgent.objects.create(name="Delegation OpenAI", api_key="key")

        result = agent.validate()

        self.assertTrue(result)
        mock_service.assert_called_once()
        self.assertEqual(mock_service.call_args[0][0], agent)

    @patch("core.models.agent.openai_completions", return_value={"text": "ok", "tokens": 1})
    def test_openai_completions_delegates_to_service(self, mock_service):
        agent = OpenAIAgent.objects.create(
            name="Delegation OpenAI Completions",
            api_key="key",
            max_tokens=1000,
        )

        result = agent.completions("hello", system_prompt="sys")

        self.assertEqual(result["text"], "ok")
        mock_service.assert_called_once()
        self.assertEqual(mock_service.call_args[0][0], agent)

    @patch("core.models.agent.deepl_validate", return_value=True)
    def test_deepl_validate_delegates_to_service(self, mock_service):
        agent = DeepLAgent.objects.create(name="Delegation DeepL", api_key="key")

        result = agent.validate()

        self.assertTrue(result)
        mock_service.assert_called_once()
        self.assertEqual(mock_service.call_args[0][0], agent)

    @patch("core.models.agent.deepl_translate", return_value={"text": "ok", "characters": 3})
    def test_deepl_translate_delegates_to_service(self, mock_service):
        agent = DeepLAgent.objects.create(name="Delegation DeepL Translate", api_key="key")

        result = agent.translate("hey", "English")

        self.assertEqual(result["text"], "ok")
        mock_service.assert_called_once()
        self.assertEqual(mock_service.call_args[0][0], agent)

    @patch("core.models.agent.libretranslate_validate", return_value=True)
    def test_libretranslate_validate_delegates_to_service(self, mock_service):
        agent = LibreTranslateAgent.objects.create(
            name="Delegation LibreValidate",
            server_url="https://example.com",
        )

        result = agent.validate()

        self.assertTrue(result)
        mock_service.assert_called_once()
        self.assertEqual(mock_service.call_args[0][0], agent)

    @patch("core.models.agent.libretranslate_translate", return_value={"text": "ok", "characters": 3})
    def test_libretranslate_translate_delegates_to_service(self, mock_service):
        agent = LibreTranslateAgent.objects.create(
            name="Delegation LibreTranslate",
            server_url="https://example.com",
        )

        result = agent.translate("hey", "English")

        self.assertEqual(result["text"], "ok")
        mock_service.assert_called_once()
        self.assertEqual(mock_service.call_args[0][0], agent)

    @patch("core.models.agent.testagent_translate", return_value={"text": "ok", "tokens": 1, "characters": 3})
    def test_testagent_translate_delegates_to_service(self, mock_service):
        agent = TestAgent.objects.create(name="Delegation TestAgent")

        result = agent.translate("hey", "English")

        self.assertEqual(result["text"], "ok")
        mock_service.assert_called_once()
        self.assertEqual(mock_service.call_args[0][0], agent)

from unittest.mock import patch
from django.contrib.admin.sites import AdminSite
from django.test import TestCase, RequestFactory

from core.admin.agent_admin import AgentAdmin, OpenAIAgentAdmin, DeepLAgentAdmin
from core.models.agent import OpenAIAgent, DeepLAgent
from utils.modelAdmin_utils import status_icon


class AgentAdminTest(TestCase):
    def setUp(self):
        self.site = AdminSite()
        self.factory = RequestFactory()
        self.openai_agent = OpenAIAgent.objects.create(
            name="Test OpenAI Agent", api_key="sk-1234567890"
        )
        self.admin = OpenAIAgentAdmin(self.openai_agent, self.site)

    @patch("core.tasks.task_manager.task_manager.submit_task")
    def test_save_model_behavior(self, mock_submit_task):
        """Test save_model success and exception handling."""
        request = self.factory.get("/admin/core/openaiahent/add/")

        # Test successful save
        self.assertIsNone(self.openai_agent.valid)
        response = self.admin.save_model(request, self.openai_agent, None, None)

        self.openai_agent.refresh_from_db()
        mock_submit_task.assert_called_once()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/core/agent")

        # Test exception handling
        mock_submit_task.side_effect = Exception("Task Error")
        mock_submit_task.reset_mock()

        response = self.admin.save_model(request, self.openai_agent, None, None)

        self.openai_agent.refresh_from_db()
        mock_submit_task.assert_called_once()
        self.assertFalse(self.openai_agent.valid)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/core/agent")

    def test_delete_model(self):
        """Test delete_model redirects and deletes object."""
        request = self.factory.delete(
            f"/admin/core/openaiahent/{self.openai_agent.pk}/delete/"
        )
        initial_count = OpenAIAgent.objects.count()

        response = self.admin.delete_model(request, self.openai_agent)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/core/agent")
        self.assertEqual(OpenAIAgent.objects.count(), initial_count - 1)
        with self.assertRaises(OpenAIAgent.DoesNotExist):
            OpenAIAgent.objects.get(pk=self.openai_agent.pk)

    def test_masked_api_key(self):
        """Test masked_api_key returns correct masked keys."""
        base_admin = AgentAdmin(self.openai_agent, self.site)

        # Test OpenAI key masking
        self.openai_agent.api_key = "sk-abcdefghijklmnopqrstuvwxyz123456"
        self.openai_agent.save()
        self.assertEqual(base_admin.masked_api_key(self.openai_agent), "sk-...456")

        # Test DeepL key masking
        deepl_agent = DeepLAgent.objects.create(
            name="Test DeepL", api_key="token-1234567890"
        )
        deepl_admin = DeepLAgentAdmin(deepl_agent, self.site)
        self.assertEqual(deepl_admin.masked_api_key(deepl_agent), "tok...890")

        # Test empty key
        self.openai_agent.api_key = ""
        self.assertEqual(base_admin.masked_api_key(self.openai_agent), "")

    def test_show_max_tokens(self):
        """Test show_max_tokens display logic."""
        self.openai_agent.max_tokens = 0
        self.assertEqual(self.admin.show_max_tokens(self.openai_agent), "Detecting...")

        self.openai_agent.max_tokens = 4096
        self.assertEqual(self.admin.show_max_tokens(self.openai_agent), 4096)

    def test_show_log(self):
        """Test show_log returns correct HTML."""
        base_admin = AgentAdmin(self.openai_agent, self.site)
        self.openai_agent.log = "Test log entry."
        self.openai_agent.save()

        html = base_admin.show_log(self.openai_agent)
        self.assertIn("<details>", html)
        self.assertIn("Test log entry.", html)

    def test_is_valid(self):
        """Test is_valid returns correct status icons."""
        base_admin = AgentAdmin(self.openai_agent, self.site)

        test_cases = [
            (True, status_icon(True)),
            (False, status_icon(False)),
            (None, status_icon(None)),
        ]

        for valid_state, expected_icon in test_cases:
            self.openai_agent.valid = valid_state
            self.assertEqual(base_admin.is_valid(self.openai_agent), expected_icon)

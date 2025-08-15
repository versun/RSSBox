from unittest.mock import patch
from django.contrib.admin.sites import AdminSite
from django.test import TestCase, RequestFactory

from core.admin.agent_admin import (
    AgentAdmin,
    OpenAIAgentAdmin,
    DeepLAgentAdmin,
)
from core.models.agent import OpenAIAgent, DeepLAgent
from utils.modelAdmin_utils import status_icon


class AgentAdminTest(TestCase):
    def setUp(self):
        self.site = AdminSite()
        self.factory = RequestFactory()
        self.openai_agent = OpenAIAgent.objects.create(name="Test OpenAI Agent", api_key="sk-1234567890")

    @patch("utils.task_manager.task_manager.submit_task")
    def test_save_model_success(self, mock_submit_task):
        """Test that save_model successfully calls validate task"""
        request = self.factory.get("/admin/core/openaiahent/add/")
        admin = OpenAIAgentAdmin(self.openai_agent, self.site)

        self.assertIsNone(self.openai_agent.valid)

        response = admin.save_model(request, self.openai_agent, None, None)

        self.openai_agent.refresh_from_db()
        mock_submit_task.assert_called_once()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/core/agent")

    @patch("utils.task_manager.task_manager.submit_task", side_effect=Exception("Task Error"))
    def test_save_model_exception(self, mock_submit_task):
        """Test that save_model handles exceptions and sets valid to False"""
        request = self.factory.get("/admin/core/openaiahent/add/")
        admin = OpenAIAgentAdmin(self.openai_agent, self.site)

        self.assertIsNone(self.openai_agent.valid)

        response = admin.save_model(request, self.openai_agent, None, None)

        self.openai_agent.refresh_from_db()
        mock_submit_task.assert_called_once()
        self.assertFalse(self.openai_agent.valid)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/core/agent")

    def test_delete_model(self):
        """Test that delete_model successfully redirects and deletes the object"""
        request = self.factory.delete(f"/admin/core/openaiahent/{self.openai_agent.pk}/delete/")
        admin = OpenAIAgentAdmin(self.openai_agent, self.site)
        initial_count = OpenAIAgent.objects.count()

        response = admin.delete_model(request, self.openai_agent)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/core/agent")
        self.assertEqual(OpenAIAgent.objects.count(), initial_count - 1)
        with self.assertRaises(OpenAIAgent.DoesNotExist):
            OpenAIAgent.objects.get(pk=self.openai_agent.pk)

    def test_masked_api_key(self):
        """Test that masked_api_key returns the correct masked key"""
        admin = AgentAdmin(self.openai_agent, self.site)
        self.openai_agent.api_key = "sk-abcdefghijklmnopqrstuvwxyz123456"
        self.openai_agent.save()

        masked_key = admin.masked_api_key(self.openai_agent)
        self.assertEqual(masked_key, "sk-...456")

        deepl_agent = DeepLAgent.objects.create(name="Test DeepL Agent", api_key="token-1234567890")
        admin_deepl = DeepLAgentAdmin(deepl_agent, self.site)
        masked_token = admin_deepl.masked_api_key(deepl_agent)
        self.assertEqual(masked_token, "tok...890")

        self.openai_agent.api_key = ""
        self.assertEqual(admin.masked_api_key(self.openai_agent), "")

    def test_show_max_tokens(self):
        """Test show_max_tokens display logic"""
        admin = OpenAIAgentAdmin(self.openai_agent, self.site)

        self.openai_agent.max_tokens = 0
        self.assertEqual(admin.show_max_tokens(self.openai_agent), "Detecting...")

        self.openai_agent.max_tokens = 4096
        self.assertEqual(admin.show_max_tokens(self.openai_agent), 4096)

    def test_show_log(self):
        """Test show_log returns correct HTML"""
        admin = AgentAdmin(self.openai_agent, self.site)
        self.openai_agent.log = "Test log entry."
        self.openai_agent.save()

        html = admin.show_log(self.openai_agent)
        self.assertIn("<details>", html)
        self.assertIn("Test log entry.", html)

    def test_is_valid(self):
        """Test is_valid returns correct status icon"""
        admin = AgentAdmin(self.openai_agent, self.site)

        self.openai_agent.valid = True
        self.assertEqual(admin.is_valid(self.openai_agent), status_icon(True))

        self.openai_agent.valid = False
        self.assertEqual(admin.is_valid(self.openai_agent), status_icon(False))

        self.openai_agent.valid = None
        self.assertEqual(admin.is_valid(self.openai_agent), status_icon(None))

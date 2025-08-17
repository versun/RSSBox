from django.test import TestCase, RequestFactory, override_settings
from django.contrib.auth.models import User
from unittest.mock import patch, MagicMock

from core.admin.admin_site import CoreAdminSite, AgentPaginator, agent_list, agent_add, core_admin_site
from core.models import Feed, Filter, Tag
from core.models.agent import OpenAIAgent, DeepLAgent


class CoreAdminSiteTestCase(TestCase):
    """Test cases for CoreAdminSite"""

    def setUp(self):
        self.admin_site = CoreAdminSite()
        self.factory = RequestFactory()
        self.user = User.objects.create_superuser('admin', 'admin@test.com', 'password')

    def test_get_app_list(self):
        """Test get_app_list method structure and content."""
        request = self.factory.get('/')
        request.user = self.user
        
        app_list = self.admin_site.get_app_list(request)
        
        # Verify basic structure
        self.assertEqual(len(app_list), 2)
        
        # First app section - core models
        first_app = app_list[0]
        self.assertEqual(first_app['app_label'], 'core')
        self.assertEqual(len(first_app['models']), 2)
        
        # Verify model entries exist and have required fields
        for model_entry in first_app['models']:
            self.assertIn('model', model_entry)
            self.assertIn('name', model_entry)
            self.assertIn('perms', model_entry)
        
        # Second app section - agents and filters
        second_app = app_list[1]
        self.assertEqual(len(second_app['models']), 2)
        
        # Check Agent entry has required URL
        agent_model = second_app['models'][0]
        self.assertEqual(agent_model['name'], 'Agents')
        self.assertEqual(agent_model['admin_url'], '/agent/list')


class AgentPaginatorTestCase(TestCase):
    """Test cases for AgentPaginator"""

    def setUp(self):
        self.paginator = AgentPaginator()

    def test_init_agent_count(self):
        """Test AgentPaginator initialization with different DEBUG settings."""
        with override_settings(DEBUG=True):
            paginator = AgentPaginator()
            self.assertEqual(paginator.agent_count, 3)
            
        with override_settings(DEBUG=False):
            paginator = AgentPaginator()
            self.assertEqual(paginator.agent_count, 2)

    def test_count_property(self):
        """Test count property returns valid integer."""
        count = self.paginator.count
        self.assertIsInstance(count, int)
        self.assertGreaterEqual(count, 2)

    @patch('core.admin.admin_site.AgentPaginator.enqueued_items')
    @patch('core.admin.admin_site.AgentPaginator._get_page')
    def test_page_method_calculations(self, mock_get_page, mock_enqueued_items):
        """Test page method offset calculations."""
        mock_enqueued_items.return_value = []
        mock_page = MagicMock()
        mock_get_page.return_value = mock_page
        
        self.paginator.page(2)
        
        # Verify offset calculation: (page-1) * per_page
        mock_enqueued_items.assert_called_once_with(100, 100)

    def test_enqueued_items_structure(self):
        """Test enqueued_items returns properly structured data."""
        OpenAIAgent.objects.create(name="Test OpenAI", api_key="test-key")
        DeepLAgent.objects.create(name="Test DeepL", api_key="test-key")
        
        items = self.paginator.enqueued_items(10, 0)
        
        self.assertIsInstance(items, list)
        if items:  # Only check structure if items exist
            required_fields = ['id', 'table_name', 'name', 'valid', 'provider']
            for field in required_fields:
                self.assertIn(field, items[0])


class AgentViewsTestCase(TestCase):
    """Test cases for agent views"""

    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_superuser('admin', 'admin@test.com', 'password')

    @patch('core.admin.admin_site.core_admin_site.each_context')
    def test_agent_list_view(self, mock_each_context):
        """Test agent_list view returns 200 response."""
        mock_each_context.return_value = {}
        
        request = self.factory.get('/agent/list?p=1')
        request.user = self.user
        
        response = agent_list(request)
        
        self.assertEqual(response.status_code, 200)
        mock_each_context.assert_called_once_with(request)

    @patch('core.admin.admin_site.core_admin_site.each_context')
    def test_agent_add_get_request(self, mock_each_context):
        """Test agent_add GET request returns 200 response."""
        mock_each_context.return_value = {}
        
        request = self.factory.get('/agent/add')
        request.user = self.user
        
        response = agent_add(request)
        
        self.assertEqual(response.status_code, 200)
        mock_each_context.assert_called_once_with(request)

    def test_agent_add_post_redirects(self):
        """Test agent_add POST requests redirect properly."""
        # Valid agent name
        request = self.factory.post('/agent/add', {'agent_name': 'openaiagent'})
        request.user = self.user
        response = agent_add(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/core/openaiagent/add')
        
        # Invalid/missing agent name
        request = self.factory.post('/agent/add', {})
        request.user = self.user
        response = agent_add(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/core///add')

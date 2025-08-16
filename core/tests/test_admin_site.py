from django.test import TestCase, RequestFactory, override_settings
from django.contrib.auth.models import User
from django.http import HttpRequest
from unittest.mock import patch, MagicMock

from core.admin.admin_site import CoreAdminSite, AgentPaginator, agent_list, agent_add, core_admin_site
from core.models import Feed, Filter, Tag
from core.models.agent import OpenAIAgent, DeepLAgent, LibreTranslateAgent, TestAgent


class CoreAdminSiteTestCase(TestCase):
    """Test cases for CoreAdminSite"""

    def setUp(self):
        self.admin_site = CoreAdminSite()
        self.factory = RequestFactory()
        self.user = User.objects.create_superuser('admin', 'admin@test.com', 'password')

    def test_get_app_list(self):
        """Test get_app_list method (lines 31-99)."""
        request = self.factory.get('/')
        request.user = self.user
        
        app_list = self.admin_site.get_app_list(request)
        
        # Verify the structure matches expected format
        self.assertEqual(len(app_list), 2)
        
        # First app section - core models
        first_app = app_list[0]
        self.assertEqual(first_app['app_label'], 'core')
        self.assertEqual(len(first_app['models']), 2)
        
        # Check Feed model entry
        feed_model = first_app['models'][0]
        self.assertEqual(feed_model['model'], Feed)
        self.assertEqual(feed_model['name'], 'Feeds')
        self.assertTrue(feed_model['perms']['add'])
        
        # Check Tag model entry  
        tag_model = first_app['models'][1]
        self.assertEqual(tag_model['model'], Tag)
        self.assertEqual(tag_model['name'], 'Tags')
        
        # Second app section - agents and filters
        second_app = app_list[1]
        self.assertEqual(len(second_app['models']), 2)
        
        # Check Agent entry
        agent_model = second_app['models'][0]
        self.assertEqual(agent_model['name'], 'Agents')
        self.assertEqual(agent_model['admin_url'], '/agent/list')
        
        # Check Filter model entry
        filter_model = second_app['models'][1]
        self.assertEqual(filter_model['model'], Filter)
        self.assertEqual(filter_model['name'], 'Filters')


class AgentPaginatorTestCase(TestCase):
    """Test cases for AgentPaginator"""

    def setUp(self):
        self.paginator = AgentPaginator()

    @override_settings(DEBUG=True)
    def test_init_debug_mode(self):
        """Test AgentPaginator initialization in debug mode (line 104)."""
        paginator = AgentPaginator()
        self.assertEqual(paginator.agent_count, 3)  # Line 106

    @override_settings(DEBUG=False)
    def test_init_production_mode(self):
        """Test AgentPaginator initialization in production mode."""
        paginator = AgentPaginator()
        self.assertEqual(paginator.agent_count, 2)

    def test_count_property(self):
        """Test count property (line 110)."""
        count = self.paginator.count
        self.assertIsInstance(count, int)
        self.assertGreaterEqual(count, 2)

    @patch('core.admin.admin_site.AgentPaginator.enqueued_items')
    @patch('core.admin.admin_site.AgentPaginator._get_page')
    def test_page_method(self, mock_get_page, mock_enqueued_items):
        """Test page method (lines 113-115)."""
        mock_enqueued_items.return_value = []
        mock_page = MagicMock()
        mock_get_page.return_value = mock_page
        
        page = self.paginator.page(2)
        
        # Verify calculations
        mock_enqueued_items.assert_called_once_with(100, 100)  # limit=100, offset=(2-1)*100
        mock_get_page.assert_called_once()

    @override_settings(DEBUG=True)
    def test_enqueued_items_debug(self):
        """Test enqueued_items method in debug mode (lines 128-146)."""
        # Create test agents
        OpenAIAgent.objects.create(name="Test OpenAI", api_key="test-key")
        DeepLAgent.objects.create(name="Test DeepL", api_key="test-key")
        
        items = self.paginator.enqueued_items(10, 0)
        
        self.assertIsInstance(items, list)
        # Should include data from created agents
        for item in items:
            self.assertIn('id', item)
            self.assertIn('table_name', item)
            self.assertIn('name', item)
            self.assertIn('valid', item)
            self.assertIn('provider', item)

    @override_settings(DEBUG=False) 
    def test_enqueued_items_production(self):
        """Test enqueued_items method in production mode."""
        # Create test agents
        OpenAIAgent.objects.create(name="Test OpenAI", api_key="test-key")
        
        items = self.paginator.enqueued_items(10, 0)
        
        self.assertIsInstance(items, list)


class AgentViewsTestCase(TestCase):
    """Test cases for agent views"""

    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_superuser('admin', 'admin@test.com', 'password')

    @patch('core.admin.admin_site.core_admin_site.each_context')
    def test_agent_list_view(self, mock_each_context):
        """Test agent_list view (lines 150-162)."""
        mock_each_context.return_value = {}
        
        request = self.factory.get('/agent/list?p=1')
        request.user = self.user
        
        response = agent_list(request)
        
        self.assertEqual(response.status_code, 200)
        mock_each_context.assert_called_once_with(request)

    @override_settings(DEBUG=True)
    @patch('core.admin.admin_site.core_admin_site.each_context')
    def test_agent_add_get_debug(self, mock_each_context):
        """Test agent_add GET request in debug mode (lines 166-193)."""
        mock_each_context.return_value = {}
        
        request = self.factory.get('/agent/add')
        request.user = self.user
        
        response = agent_add(request)
        
        self.assertEqual(response.status_code, 200)
        mock_each_context.assert_called_once_with(request)

    @override_settings(DEBUG=False)
    @patch('core.admin.admin_site.core_admin_site.each_context')  
    def test_agent_add_get_production(self, mock_each_context):
        """Test agent_add GET request in production mode."""
        mock_each_context.return_value = {}
        
        request = self.factory.get('/agent/add')
        request.user = self.user
        
        response = agent_add(request)
        
        self.assertEqual(response.status_code, 200)

    def test_agent_add_post_valid_redirect(self):
        """Test agent_add POST request with valid agent name."""
        request = self.factory.post('/agent/add', {'agent_name': 'openaiagent'})
        request.user = self.user
        
        response = agent_add(request)
        
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/core/openaiagent/add')

    def test_agent_add_post_invalid_redirect(self):
        """Test agent_add POST request with invalid/missing agent name."""
        request = self.factory.post('/agent/add', {})
        request.user = self.user
        
        response = agent_add(request)
        
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/core///add')  # Default agent_name is "/", creates this URL

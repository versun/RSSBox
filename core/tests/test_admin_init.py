from django.test import TestCase, override_settings
from django.contrib.auth.models import User, Group
from core.admin.admin_site import core_admin_site


class AdminInitTestCase(TestCase):
    """Test cases for core/admin/__init__.py"""

    def _reload_admin_module(self):
        """Helper to reload admin module for testing."""
        import importlib
        import core.admin
        importlib.reload(core.admin)

    @override_settings(USER_MANAGEMENT=True)
    def test_user_management_enabled(self):
        """Test User and Group registration when USER_MANAGEMENT is True."""
        self._reload_admin_module()
        self.assertIn(User, core_admin_site._registry)
        self.assertIn(Group, core_admin_site._registry)

    @override_settings(USER_MANAGEMENT=False)
    def test_user_management_disabled(self):
        """Test User and Group are not registered when USER_MANAGEMENT is False."""
        self._reload_admin_module()
        self.assertNotIn(User, core_admin_site._registry)
        self.assertNotIn(Group, core_admin_site._registry)

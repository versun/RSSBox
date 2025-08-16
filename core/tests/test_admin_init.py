from django.test import TestCase, override_settings
from django.contrib.auth.models import User, Group
from core.admin.admin_site import core_admin_site


class AdminInitTestCase(TestCase):
    """Test cases for core/admin/__init__.py"""

    @override_settings(USER_MANAGEMENT=True)
    def test_user_management_enabled(self):
        """Test User and Group registration when USER_MANAGEMENT is True (lines 10-11)."""
        # Import the module to trigger the registration
        import importlib
        import core.admin
        importlib.reload(core.admin)
        
        # Check if User and Group are registered
        self.assertIn(User, core_admin_site._registry)  # Line 10
        self.assertIn(Group, core_admin_site._registry)  # Line 11

    @override_settings(USER_MANAGEMENT=False)
    def test_user_management_disabled(self):
        """Test User and Group are not registered when USER_MANAGEMENT is False."""
        # Import the module to trigger the registration check
        import importlib
        import core.admin
        importlib.reload(core.admin)
        
        # Check if User and Group are NOT registered
        self.assertNotIn(User, core_admin_site._registry)
        self.assertNotIn(Group, core_admin_site._registry)

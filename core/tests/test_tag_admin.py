from django.test import TestCase, RequestFactory
from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import User

from core.admin.tag_admin import TagAdmin
from core.models import Tag, Filter


class TagAdminTestCase(TestCase):
    """Test cases for TagAdmin"""

    def setUp(self):
        self.factory = RequestFactory()
        self.admin_site = AdminSite()
        self.admin = TagAdmin(Tag, self.admin_site)
        self.user = User.objects.create_superuser('admin', 'admin@test.com', 'password')
        
        self.tag = Tag.objects.create(name="Test Tag")

    def test_show_filters_no_filters(self):
        """Test show_filters when no filters exist (lines 31-32)."""
        result = self.admin.show_filters(self.tag)
        self.assertEqual(result, "-")

    def test_show_filters_with_filters(self):
        """Test show_filters with existing filters (lines 33-37)."""
        # Create some filters
        filter1 = Filter.objects.create(
            name="Filter 1",
            filter_method=Filter.KEYWORD_ONLY
        )
        filter2 = Filter.objects.create(
            name="Filter 2", 
            filter_method=Filter.AI_ONLY
        )
        
        # Associate filters with tag
        self.tag.filters.add(filter1, filter2)
        
        result = self.admin.show_filters(self.tag)
        
        self.assertIn("Filter 1", result)
        self.assertIn("Filter 2", result)
        self.assertIn("/core/filter/", result)  # Check for actual URL path
        self.assertIn("<br>", result)

    def test_show_url_with_pk(self):
        """Test show_url when tag has pk (lines 41-46)."""
        result = self.admin.show_url(self.tag)
        
        self.assertIn("rss", result)
        self.assertIn("json", result)
        self.assertIn(f"/rss/tag/{self.tag.slug}", result)
        self.assertIn(f"/rss/tag/json/{self.tag.slug}", result)
        self.assertIn("target='_blank'", result)

    def test_show_url_without_pk(self):
        """Test show_url when tag has no pk (lines 47-48)."""
        # Create tag without saving to database
        new_tag = Tag(name="Unsaved Tag")
        
        result = self.admin.show_url(new_tag)
        
        self.assertEqual(result, "-")

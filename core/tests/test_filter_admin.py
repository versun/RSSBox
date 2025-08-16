from django.test import TestCase, RequestFactory
from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import User

from core.admin.filter_admin import FilterAdmin
from core.models import Filter


class FilterAdminTestCase(TestCase):
    """Test cases for FilterAdmin"""

    def setUp(self):
        self.factory = RequestFactory()
        self.admin_site = AdminSite()
        self.admin = FilterAdmin(Filter, self.admin_site)
        self.user = User.objects.create_superuser('admin', 'admin@test.com', 'password')
        
        self.filter = Filter.objects.create(
            name="Test Filter",
            filter_method=Filter.KEYWORD_ONLY,
            total_tokens=1500
        )

    def test_get_queryset_prefetch_related(self):
        """Test get_queryset method prefetches keywords (line 47)."""
        request = self.factory.get('/')
        request.user = self.user
        
        queryset = self.admin.get_queryset(request)
        
        # Verify prefetch_related was called by checking the queryset
        self.assertIn('keywords', queryset._prefetch_related_lookups)

    def test_show_keywords_empty(self):
        """Test show_keywords when no keywords exist (line 52)."""
        # Filter with no keywords - need to check actual return value
        result = self.admin.show_keywords(self.filter)
        self.assertEqual(result, "<span title=''></span>")  # Actual behavior

    def test_show_keywords_with_keywords(self):
        """Test show_keywords with existing keywords."""
        # Set keywords using Tagulous string format
        self.filter.keywords = "keyword1, keyword2"
        self.filter.save()
        
        result = self.admin.show_keywords(self.filter)
        
        self.assertIn("keyword1", result)
        self.assertIn("keyword2", result)
        self.assertIn("title=", result)  # Should have tooltip

    def test_show_keywords_truncation(self):
        """Test show_keywords truncates long keyword lists."""
        # Create more than 10 keywords using Tagulous string format
        keywords = [f"keyword{i}" for i in range(12)]
        self.filter.keywords = ", ".join(keywords)
        self.filter.save()
        
        result = self.admin.show_keywords(self.filter)
        
        # Should show "..." for truncation
        self.assertIn("...", result)

    def test_tokens_info_under_1000(self):
        """Test tokens_info method for numbers under 1000 (lines 63-73)."""
        self.filter.total_tokens = 500
        
        result = self.admin.tokens_info(self.filter)
        
        self.assertIn("500", result)

    def test_tokens_info_thousands(self):
        """Test tokens_info method for thousands (lines 66-68)."""
        self.filter.total_tokens = 2500
        
        result = self.admin.tokens_info(self.filter)
        
        self.assertIn("2.5K", result)

    def test_tokens_info_exact_thousands(self):
        """Test tokens_info method for exact thousands (replace .0K with K)."""
        self.filter.total_tokens = 3000
        
        result = self.admin.tokens_info(self.filter)
        
        self.assertIn("3K", result)  # Should be "3K", not "3.0K"

    def test_tokens_info_millions(self):
        """Test tokens_info method for millions (lines 69-71)."""
        self.filter.total_tokens = 2500000
        
        result = self.admin.tokens_info(self.filter)
        
        self.assertIn("2.5M", result)

    def test_tokens_info_exact_millions(self):
        """Test tokens_info method for exact millions (replace .0M with M)."""
        self.filter.total_tokens = 3000000
        
        result = self.admin.tokens_info(self.filter)
        
        self.assertIn("3M", result)  # Should be "3M", not "3.0M"

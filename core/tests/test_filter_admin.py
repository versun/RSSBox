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

    def test_show_keywords_scenarios(self):
        """Test show_keywords for different keyword scenarios."""
        # Test empty keywords
        result = self.admin.show_keywords(self.filter)
        self.assertEqual(result, "<span title=''></span>")
        
        # Test with keywords
        self.filter.keywords = "keyword1, keyword2"
        self.filter.save()
        result = self.admin.show_keywords(self.filter)
        self.assertIn("keyword1", result)
        self.assertIn("keyword2", result)
        self.assertIn("title=", result)
        
        # Test truncation with many keywords
        keywords = [f"keyword{i}" for i in range(12)]
        self.filter.keywords = ", ".join(keywords)
        self.filter.save()
        result = self.admin.show_keywords(self.filter)
        self.assertIn("...", result)

    def test_tokens_info_formatting(self):
        """Test tokens_info method for different number formats."""
        test_cases = [
            (500, "500"),
            (2500, "2.5K"),
            (3000, "3K"),
            (2500000, "2.5M"),
            (3000000, "3M")
        ]
        
        for tokens, expected in test_cases:
            with self.subTest(tokens=tokens):
                self.filter.total_tokens = tokens
                result = self.admin.tokens_info(self.filter)
                self.assertIn(expected, result)

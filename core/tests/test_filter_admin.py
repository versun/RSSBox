from django.test import TestCase, RequestFactory
from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import User
from django.utils.html import format_html

from core.admin.filter_admin import FilterAdmin
from core.models import Filter
from core.forms import FilterForm
from core.actions import clean_filter_results


class FilterAdminTestCase(TestCase):
    """Test cases for FilterAdmin"""

    def setUp(self):
        self.factory = RequestFactory()
        self.admin_site = AdminSite()
        self.admin = FilterAdmin(Filter, self.admin_site)
        self.user = User.objects.create_superuser('admin', 'admin@test.com', 'password')
        
        # 创建测试用的标签名称（字符串）
        self.tag1_name = "keyword1"
        self.tag2_name = "keyword2"
        self.tag3_name = "keyword3"
        
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

    def test_show_keywords_empty_keywords(self):
        """Test show_keywords when keywords is empty (covers line 51-52)."""
        # 确保filter没有关键词
        self.filter.keywords.clear()
        result = self.admin.show_keywords(self.filter)
        self.assertEqual(result, "<span title=''></span>")

    def test_show_keywords_none_keywords(self):
        """Test show_keywords when keywords is None (covers line 51-52)."""
        # 创建一个新的filter，不设置keywords
        filter_no_keywords = Filter.objects.create(
            name="Filter No Keywords",
            filter_method=Filter.KEYWORD_ONLY,
            total_tokens=0
        )
        result = self.admin.show_keywords(filter_no_keywords)
        self.assertEqual(result, "<span title=''></span>")

    def test_show_keywords_with_keywords(self):
        """Test show_keywords with keywords (covers lines 54-59)."""
        # 添加关键词到filter（使用字符串）
        self.filter.keywords.add(self.tag1_name, self.tag2_name)
        
        result = self.admin.show_keywords(self.filter)
        
        # 验证结果包含关键词
        self.assertIn("keyword1", result)
        self.assertIn("keyword2", result)
        self.assertIn("title=", result)
        
        # 验证HTML结构
        self.assertIn("<span", result)
        self.assertIn("</span>", result)

    def test_show_keywords_truncation_with_many_keywords(self):
        """Test show_keywords truncation when more than 10 keywords (covers line 58)."""
        # 创建超过10个关键词（使用字符串）
        keywords = []
        for i in range(12):
            keywords.append(f"keyword{i}")
        
        self.filter.keywords.add(*keywords)
        
        result = self.admin.show_keywords(self.filter)
        
        # 验证截断逻辑
        self.assertIn("...", result)
        # 验证只显示前10个关键词（tagulous 按字母顺序排序）
        # keyword0, keyword1, keyword10, keyword11, keyword2, keyword3, keyword4, keyword5, keyword6, keyword7, keyword8, keyword9
        # 前10个应该是：keyword0, keyword1, keyword10, keyword11, keyword2, keyword3, keyword4, keyword5, keyword6, keyword7
        self.assertIn("keyword0", result)
        self.assertIn("keyword1", result)
        self.assertIn("keyword10", result)  # 按字母顺序，keyword10 在 keyword2 之前
        self.assertIn("keyword11", result)  # 按字母顺序，keyword11 在 keyword2 之前
        self.assertIn("keyword2", result)
        # 验证第12个关键词不在显示中（但可能在title中）
        # 由于按字母顺序，keyword8 和 keyword9 可能不在前10个中
        # 我们只需要验证有省略号，说明超过了10个关键词
        self.assertIn("...", result)

    def test_show_keywords_exactly_10_keywords(self):
        """Test show_keywords when exactly 10 keywords (covers line 58)."""
        # 创建恰好10个关键词（使用字符串）
        keywords = []
        for i in range(10):
            keywords.append(f"keyword{i}")
        
        self.filter.keywords.add(*keywords)
        
        result = self.admin.show_keywords(self.filter)
        
        # 验证不显示省略号
        self.assertNotIn("...", result)
        # 验证显示所有关键词
        for i in range(10):
            self.assertIn(f"keyword{i}", result)

    def test_tokens_info_small_numbers(self):
        """Test tokens_info for numbers less than 1000 (covers lines 64-65)."""
        test_cases = [0, 500, 999]
        
        for tokens in test_cases:
            with self.subTest(tokens=tokens):
                self.filter.total_tokens = tokens
                result = self.admin.tokens_info(self.filter)
                expected = f"<span>{tokens}</span>"
                self.assertEqual(result, expected)

    def test_tokens_info_thousands_formatting(self):
        """Test tokens_info for numbers in thousands (covers lines 66-68)."""
        test_cases = [
            (1000, "1K"),
            (1500, "1.5K"),
            (9999, "10K"),
            (10000, "10K"),
            (999999, "1000K")
        ]
        
        for tokens, expected in test_cases:
            with self.subTest(tokens=tokens):
                self.filter.total_tokens = tokens
                result = self.admin.tokens_info(self.filter)
                self.assertIn(expected, result)

    def test_tokens_info_millions_formatting(self):
        """Test tokens_info for numbers in millions (covers lines 69-71)."""
        test_cases = [
            (1000000, "1M"),
            (1500000, "1.5M"),
            (9999999, "10M"),
            (10000000, "10M")
        ]
        
        for tokens, expected in test_cases:
            with self.subTest(tokens=tokens):
                self.filter.total_tokens = tokens
                result = self.admin.tokens_info(self.filter)
                self.assertIn(expected, result)

    def test_tokens_info_edge_cases(self):
        """Test tokens_info for edge cases (covers all branches)."""
        # 测试边界值
        edge_cases = [
            (999, "999"),      # 小于1000
            (1000, "1K"),      # 等于1000
            (999999, "1000K"),  # 小于1000000
            (1000000, "1M"),    # 等于1000000
        ]
        
        for tokens, expected in edge_cases:
            with self.subTest(tokens=tokens):
                self.filter.total_tokens = tokens
                result = self.admin.tokens_info(self.filter)
                self.assertIn(expected, result)

    def test_show_keywords_mixed_scenarios(self):
        """Test show_keywords with various keyword combinations."""
        # 测试单个关键词
        self.filter.keywords.clear()
        self.filter.keywords.add(self.tag1_name)
        result = self.admin.show_keywords(self.filter)
        self.assertIn("keyword1", result)
        self.assertNotIn("...", result)
        
        # 测试两个关键词
        self.filter.keywords.add(self.tag2_name)
        result = self.admin.show_keywords(self.filter)
        self.assertIn("keyword1", result)
        self.assertIn("keyword2", result)
        self.assertNotIn("...", result)

    def test_admin_configuration(self):
        """Test FilterAdmin configuration attributes."""
        self.assertEqual(self.admin.change_form_template, "admin/change_form_with_tabs.html")
        self.assertIn("tokens_info", self.admin.list_display)
        self.assertIn("name", self.admin.search_fields)
        self.assertIn("keywords__name", self.admin.search_fields)
        self.assertIn("tokens_info", self.admin.readonly_fields)
        self.assertEqual(self.admin.form, FilterForm)
        self.assertIn(clean_filter_results, self.admin.actions)

    def test_fieldsets_structure(self):
        """Test FilterAdmin fieldsets configuration."""
        expected_fieldsets = 3
        self.assertEqual(len(self.admin.fieldsets), expected_fieldsets)
        
        # 验证第一个fieldset
        first_fieldset = self.admin.fieldsets[0]
        self.assertEqual(first_fieldset[0], "Filter Information")
        self.assertIn("name", first_fieldset[1]["fields"])
        self.assertIn("filter_method", first_fieldset[1]["fields"])
        self.assertIn("target_field", first_fieldset[1]["fields"])
        
        # 验证第二个fieldset
        second_fieldset = self.admin.fieldsets[1]
        self.assertEqual(second_fieldset[0], "Keywords")
        self.assertIn("keywords", second_fieldset[1]["fields"])
        self.assertIn("operation", second_fieldset[1]["fields"])
        
        # 验证第三个fieldset
        third_fieldset = self.admin.fieldsets[2]
        self.assertEqual(third_fieldset[0], "AI")
        self.assertIn("agent_option", third_fieldset[1]["fields"])
        self.assertIn("filter_prompt", third_fieldset[1]["fields"])
        self.assertIn("tokens_info", third_fieldset[1]["fields"])

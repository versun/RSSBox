import logging
from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from django.utils.html import format_html
from core.models import Filter
from core.forms import FilterForm
from core.admin import core_admin_site
from core.actions import clean_filter_results


class FilterAdmin(admin.ModelAdmin):
    change_form_template = "admin/change_form_with_tabs.html"
    list_display = ("name", "filter_method", "operation", "tokens_info")
    search_fields = ("name", "keywords__name")
    readonly_fields = ("tokens_info",)
    form = FilterForm
    actions = [clean_filter_results]
    fieldsets = (
        (
            _("Filter Information"),
            {
                "fields": (
                    "name",
                    "filter_method",
                    "target_field",
                )
            },
        ),
         (
            _("Keywords"),
            {
                "fields": (
                    "keywords",
                    "operation",
                )
            },
        ),
         (
            _("AI"),
            {
                "fields": (
                    "agent_option",
                    "filter_prompt",
                    "tokens_info"
                )
            },
        ),
    )   

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("keywords")

    @admin.display(description=_("Keywords"))
    def show_keywords(self, obj):
        if not obj.keywords:
            return ""
        # keywords = ", ".join([force_str(keyword) for keyword in obj.keywords.all()])
        keywords = obj.keywords.all().values_list("name", flat=True)
        return format_html(
            "<span title='{}'>{}</span>",
            ", ".join(keywords),  # Full list as tooltip
            ", ".join(keywords[:10]) + ("..." if len(keywords) > 10 else ""),
        )

    @admin.display(description=_("Tokens Info"))
    def tokens_info(self, obj):
        def format_number(n):
            if n < 1000:
                return str(n)
            elif n < 1000000:
                # 避免显示不必要的小数点
                return f"{n/1000:.1f}K".replace(".0K", "K")
            else:
                # 百万单位格式化
                return f"{n/1000000:.1f}M".replace(".0M", "M")
        
        return format_html(
            "<span>{}</span>",
            format_number(obj.total_tokens),
        )


core_admin_site.register(Filter, FilterAdmin)

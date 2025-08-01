import logging
from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from django.utils.html import format_html
from core.models import Filter
from core.forms import FilterForm
from core.admin import core_admin_site


class FilterAdmin(admin.ModelAdmin):
    list_display = ("name", "show_keywords")
    search_fields = ("name", "keywords__name")
    form = FilterForm

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("keywords")

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

    show_keywords.short_description = _("Keywords")


core_admin_site.register(Filter, FilterAdmin)

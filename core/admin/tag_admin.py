import logging
from django.contrib import admin
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from django.urls import  reverse
from core.models import Tag
from utils.task_manager import task_manager
from core.admin import core_admin_site
from core.actions import tag_force_update

class TagAdmin(admin.ModelAdmin):
    list_display = ("name","show_url","show_filters","last_updated",)
    search_fields = ["name","slug"]
    readonly_fields = ["total_tokens","last_updated","etag","show_url"]
    autocomplete_fields = ["filters",]
    fields = ["name","filters","show_url","last_updated"]
    actions = [tag_force_update]

    @admin.display(description=_("Filters"))
    def show_filters(self, obj):
        if not obj.filters.exists():
            return "-"
        filters_html = "<br>".join(
            f"<a href='{reverse('admin:core_filter_change', args=[f.id])}'>{f.name}</a>"
            for f in obj.filters.all()
        )
        return format_html(filters_html)

    @admin.display(description="URL")
    def show_url(self, obj):
         if obj.pk:
            return format_html(
                "<a href='{0}' target='_blank'>rss</a> | <a href='{1}' target='_blank'>json</a>",
                f"/rss/tag/{obj.slug}",
                f"/rss/tag/json/{obj.slug}",
            )
         else:
             return "-"
        
core_admin_site.register(Tag, TagAdmin)
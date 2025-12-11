import logging
from django.contrib import admin
from django.utils.html import format_html, mark_safe
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
from django.urls import path, reverse
from django.db import transaction
from core.models import Feed
from core.forms import FeedForm
from core.actions import (
    export_original_feed_as_opml,
    export_translated_feed_as_opml,
    feed_force_update,
    feed_batch_modify,
    clean_translated_content,
    clean_ai_summary,
)
from utils.modelAdmin_utils import status_icon
from core.tasks.task_manager import task_manager
from core.views import import_opml
from core.management.commands.feed_updater import update_single_feed
from core.admin import core_admin_site

logger = logging.getLogger(__name__)


class FeedAdmin(admin.ModelAdmin):
    change_form_template = "admin/change_form_with_tabs.html"
    form = FeedForm
    list_display = [
        "name",
        "fetch_feed",
        "generate_feed",
        "translator",
        "translation_options",
        "show_filters",
        "fetch_info",
        "cost_info",
        "show_tags",
        "target_language",
    ]
    search_fields = ["name", "feed_url", "slug", "author", "link"]
    list_filter = [
        "tags",
        "fetch_status",
        "translation_status",
        "translate_title",
        "translate_content",
        "summary",
    ]
    readonly_fields = [
        "fetch_feed",
        "generate_feed",
        "fetch_status",
        "translation_status",
        "total_tokens",
        "total_characters",
        "last_fetch",
        "last_translate",
        "show_log",
    ]
    autocomplete_fields = ["filters", "tags"]
    fieldsets = (
        # 基础信息组（始终可见）
        (
            _("Feed Information"),
            {
                "fields": (
                    "feed_url",
                    "name",
                    "max_posts",
                    "simple_update_frequency",
                    "tags",
                    "fetch_article",
                    "show_log",
                ),
                # "description": "内容源的基本识别信息和限制设置"
            },
        ),
        # 内容处理组
        (
            _("Content Processing"),
            {
                "fields": (
                    "target_language",
                    "translate_title",
                    "translate_content",
                    "summary",
                    "translator_option",
                    "summarizer",
                    "summary_detail",
                    "additional_prompt",
                ),
            },
        ),
        # 输出控制组
        (
            _("Output Control"),
            {
                "fields": (
                    "slug",
                    "translation_display",
                    "filters",
                ),
            },
        ),
        (
            _("Status"),
            {
                "fields": (
                    "fetch_status",
                    "translation_status",
                    "total_tokens",
                    "total_characters",
                    "last_fetch",
                    "last_translate",
                ),
            },
        ),
    )
    actions = [
        feed_force_update,
        export_original_feed_as_opml,
        export_translated_feed_as_opml,
        feed_batch_modify,
        clean_translated_content,
        clean_ai_summary,
    ]
    list_per_page = 20

    def get_queryset(self, request):
        """
        过滤掉系统生成的 Digest Feed，只显示用户添加的普通 Feed。
        Digest Feed 的 feed_url 包含 '/core/digest/rss/' 路径。
        """
        queryset = super().get_queryset(request)
        # 过滤掉 Digest Feed
        return queryset.exclude(author="RSSBox Digest")

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "import_opml/",
                self.admin_site.admin_view(import_opml),
                name="core_feed_import_opml",
            ),
        ]
        return custom_urls + urls

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context["import_opml_button"] = format_html(
            '<a class="button" href="{}">导入OPML</a>',
            reverse("admin:core_feed_import_opml"),
        )
        return super().changelist_view(request, extra_context=extra_context)

    def save_model(self, request, obj, form, change):
        logger.info(f"Call Feed save_model: {obj}")
        needs_reprocessing = any(
            field in form.changed_data
            for field in [
                "feed_url",  # 需要重新抓取
                "target_language",  # 需要重新翻译
                "translate_title",  # 需要重新翻译标题
                "translate_content",  # 需要重新翻译内容
                "summary",  # 需要重新生成摘要
                "translator_option",  # 需要用新翻译器重新翻译
                "summarizer",  # 需要用新引擎重新生成摘要
                "additional_prompt",
            ]
        )
        feed_url_changed = "feed_url" in form.changed_data
        target_language_changed = "target_language" in form.changed_data
        # 处理默认名称设置
        obj.name = obj.name or ("Loading" if needs_reprocessing else "Empty")
        # 无需特殊处理的情况直接保存返回
        if not needs_reprocessing:
            logger.info("No reprocessing needed, saving and returning")
            super().save_model(request, obj, form, change)
            return

        logger.info("Reprocessing needed, proceeding with task submission")
        # 需要触发任务的处理流程
        obj.fetch_status = None
        obj.translation_status = None

        super().save_model(request, obj, form, change)

        # 处理条目数据变更
        if target_language_changed:
            obj.entries.update(
                translated_content=None, translated_title=None, ai_summary=None
            )
        if feed_url_changed:
            obj.entries.all().delete()

        from functools import partial

        transaction.on_commit(partial(self._submit_feed_update_task, obj))

    def _submit_feed_update_task(self, feed):
        task_id = task_manager.submit_task(
            f"update_feed_{feed.slug}", update_single_feed, feed
        )
        logger.info(f"Submitted feed update task after commit: {task_id}")

    @admin.display(description=_("Name"))
    def show_name(self, obj):
         return format_html(
            "<a href='{0}'>{1}</a><br><sub>->{2}</sub>",
            reverse("admin:core_feed_change", args=[obj.id]),
            obj.name,
            obj.target_language,
        )

    @admin.display(description=_("Update Frequency"), ordering="update_frequency")
    def simple_update_frequency(self, obj):
        if obj.update_frequency <= 5:
            return "5 min"
        elif obj.update_frequency <= 15:
            return "15 min"
        elif obj.update_frequency <= 30:
            return "30 min"
        elif obj.update_frequency <= 60:
            return "hourly"
        elif obj.update_frequency <= 1440:
            return "daily"
        elif obj.update_frequency <= 10080:
            return "weekly"

    @admin.display(description=_("Translator"))
    def translator(self, obj):
        return obj.translator

    @admin.display(description=_("Generate"))
    def generate_feed(self, obj):  # 显示3个元素：translated_status、feed_url、json_url
        if not obj.translate_title and not obj.translate_content and not obj.summary:
            translation_status_icon = "-"
        else:
            translation_status_icon = status_icon(obj.translation_status)
        return format_html(
            "<span>{0}</span><br><a href='{1}' target='_blank'>{2}</a> | <a href='{3}' target='_blank'>{4}</a>",
            translation_status_icon,  # 0
            f"/rss/{obj.slug}",  # 1
            "rss",  # 2
            f"/rss/json/{obj.slug}",  # 3
            "json",  # 4
        )

    @admin.display(description=_("Fetch"))
    def fetch_feed(self, obj):  # 显示3个元素：fetch状态、原url、代理feed
        if obj.pk:
            status = status_icon(obj.fetch_status)
        else:
            status = "-"
        return format_html(
            "<span>{0}</span><br><a href='{1}' target='_blank'>{2}</a> | <a href='{3}' target='_blank'>{4}</a>",
            status,  # 0
            obj.feed_url,  # 1
            "url",  # 2
            f"/rss/proxy/{obj.slug}",  # 3
            "proxy",  # 4
        )

    @admin.display(description=_("Tasks"))
    def translation_options(self, obj):
        html_content = ""
        if obj.translate_title:
            html_content += f"✔️{_('Title')}<br>"
        if obj.translate_content:
            html_content += f"✔️{_('Content')}<br>"
        if obj.summary:
            html_content += f"✔️{_('Summary')}<br>"
        return format_html("{}", mark_safe(html_content))

    @admin.display(description=_("Log"))
    def show_log(self, obj):
        return format_html(
            """
            <details>
                <summary>show</summary>
                <div style="max-height: 200px; overflow: auto;">
                    {0}
                </div>
            </details>
            """,
            mark_safe(obj.log),
        )

    @admin.display(description=_("Fetch Info"))
    def fetch_info(self, obj):
        if obj.last_fetch:
            # 将 UTC 时间转换为本地时区
            local_time = timezone.localtime(obj.last_fetch)
            time_str = local_time.strftime("%Y-%m-%d %H:%M:%S")
        else:
            time_str = "-"
        
        return format_html(
            "<span>{0}</span><br><span>{1}</span>",
            self.simple_update_frequency(obj),
            time_str,
        )

    @admin.display(description=_("Cost Info"))
    def cost_info(self, obj):
        def format_number(n):
            if n < 1000:
                return str(n)
            elif n < 1000000:
                # 避免显示不必要的小数点
                return f"{n / 1000:.1f}K".replace(".0K", "K")
            else:
                # 百万单位格式化
                return f"{n / 1000000:.1f}M".replace(".0M", "M")

        return format_html(
            "<span>tokens:{}</span><br><span>characters:{}</span>",
            format_number(obj.total_tokens),
            format_number(obj.total_characters),
        )

    @admin.display(description=_("Filters"))
    def show_filters(self, obj):
        if not obj.filters.exists():
            return "-"
        filters_html = "<br>".join(
            f"<a href='{reverse('admin:core_filter_change', args=[f.id])}'>{f.name}</a>"
            for f in obj.filters.all()
        )
        return format_html("{}", mark_safe(filters_html))

    @admin.display(description=_("tags"))
    def show_tags(self, obj):
        if not obj.tags.exists():  # obj.tags 返回一个QuerySet对象，bool(obj.tags) 总是True，因为QuerySet对象总是被认为是True
            return "-"
        tags_html = "<br>".join(
            f"<a href='{reverse('admin:core_tag_change', args=[t.id])}'>#{t.name}</a>"
            for t in obj.tags.all()
        )
        return format_html("{}", mark_safe(tags_html))


core_admin_site.register(Feed, FeedAdmin)

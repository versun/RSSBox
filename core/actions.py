import logging
from django.utils import timezone

from django.contrib import admin, messages
from django.shortcuts import render, redirect
from django.http import HttpResponse
from django.db import transaction
from django.utils.translation import gettext_lazy as _
from django.conf import settings
from utils.modelAdmin_utils import get_all_agent_choices
from core.admin import core_admin_site
from core.models import Filter, Tag, OpenAIAgent
from core.tasks.task_manager import task_manager
from .management.commands.feed_updater import update_multiple_feeds
from core.cache import cache_tag
from core.services.admin import force_update_feeds, force_update_tags
from core.services.admin.batch import (
    apply_batch_updates,
    build_batch_modify_context,
)
from core.services.opml import build_opml_response

logger = logging.getLogger(__name__)


@admin.display(description=_("Clean translated content"))
def clean_translated_content(modeladmin, request, queryset):
    for feed in queryset:
        # 更新该feed下所有entry的翻译相关字段为None或空字符串
        feed.entries.all().update(translated_title=None, translated_content=None)
    modeladmin.message_user(
        request,
        _("Successfully cleaned translated content for selected feeds."),
        messages.SUCCESS,
    )


@admin.display(description=_("Clean ai summary"))
def clean_ai_summary(modeladmin, request, queryset):
    for feed in queryset:
        # 更新该feed下所有entry的翻译相关字段为None或空字符串
        feed.entries.all().update(ai_summary=None)
    modeladmin.message_user(
        request,
        _("Successfully cleaned ai summary for selected feeds."),
        messages.SUCCESS,
    )


@admin.display(description=_("Clean filter results"))
def clean_filter_results(modeladmin, request, queryset):
    for filter in queryset:
        filter.clear_ai_filter_cache_results()

    modeladmin.message_user(
        request,
        _("Successfully cleaned all filter results for selected filters."),
        messages.SUCCESS,
    )


def _generate_opml_feed(title_prefix, queryset, get_feed_url_func, filename_prefix):
    try:
        return build_opml_response(
            title_prefix=title_prefix,
            queryset=queryset,
            get_feed_url_func=get_feed_url_func,
            filename_prefix=filename_prefix,
        )
    except Exception as e:
        logger.error("OPML export error: %s", str(e), exc_info=True)
        return HttpResponse("An error occurred during OPML export", status=500)


@admin.display(description=_("Export selected original feeds as OPML"))
def export_original_feed_as_opml(modeladmin, request, queryset):
    """导出原始订阅源为OPML文件"""
    return _generate_opml_feed(
        title_prefix="Original Feeds",
        queryset=queryset,
        get_feed_url_func=lambda feed: feed.feed_url,
        filename_prefix="original",
    )


@admin.display(description=_("Export selected translated feeds as OPML"))
def export_translated_feed_as_opml(modeladmin, request, queryset):
    """导出翻译后的订阅源为OPML文件"""
    return _generate_opml_feed(
        title_prefix="Translated Feeds",
        queryset=queryset,
        get_feed_url_func=lambda feed: f"{settings.SITE_URL}/rss/{feed.slug}",
        filename_prefix="translated",
    )


@admin.display(description=_("Force update"))
def feed_force_update(modeladmin, request, queryset):
    logger.info("Call feed_force_update: %s", queryset)
    force_update_feeds(
        queryset,
        task_manager=task_manager,
        update_multiple_feeds_func=update_multiple_feeds,
    )


@admin.display(description=_("Recombine related feeds."))
def tag_force_update(modeladmin, request, queryset):
    logger.info("Call tag_force_update: %s", queryset)
    force_update_tags(
        queryset,
        task_manager=task_manager,
        cache_tag_func=cache_tag,
        now_func=timezone.now,
    )


@admin.display(description=_("Batch modification"))
def feed_batch_modify(modeladmin, request, queryset):
    if "apply" in request.POST:
        logger.info("Apply feed_batch_modify")
        apply_batch_updates(queryset, request.POST)
        return redirect(request.get_full_path())

    return render(
        request,
        "admin/feed_batch_modify.html",
        context=build_batch_modify_context(
            queryset,
            get_all_agent_choices_func=get_all_agent_choices,
            openai_agent_model=OpenAIAgent,
            filter_model=Filter,
            tag_model=Tag,
            settings_module=settings,
            admin_context=core_admin_site.each_context(request),
        ),
    )

import logging
from datetime import datetime
from django.utils import timezone

from ast import literal_eval
from django.contrib import admin, messages
from django.shortcuts import render, redirect
from django.http import HttpResponse, HttpResponseRedirect
from django.db import transaction
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.conf import settings
from lxml import etree
from utils.modelAdmin_utils import get_all_agent_choices, get_ai_agent_choices
from core.admin import core_admin_site
from core.models import Filter, Tag
from core.tasks.task_manager import task_manager
from .management.commands.update_feeds import update_multiple_feeds
from core.cache import cache_tag

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
    """
    生成OPML文件的通用函数

    Args:
        title_prefix (str): OPML标题前缀
        queryset (QuerySet): 要导出的数据集合
        get_feed_url_func (function): 获取feed URL的函数
        filename_prefix (str): 导出文件名前缀

    Returns:
        HttpResponse: 包含OPML文件的响应或错误响应
    """
    try:
        # 创建根元素 <opml> 并设置版本
        root = etree.Element("opml", version="2.0")

        # 创建头部 <head>
        head = etree.SubElement(root, "head")
        etree.SubElement(head, "title").text = f"{title_prefix} | RSS Translator"
        etree.SubElement(head, "dateCreated").text = datetime.now().strftime(
            "%a, %d %b %Y %H:%M:%S %z"
        )
        etree.SubElement(head, "ownerName").text = "RSS Translator"

        # 创建主体 <body>
        body = etree.SubElement(root, "body")

        # 按分类组织订阅源
        categories = {}
        for feed in queryset:
            feed_tags = list(feed.tags.all()) or [
                None
            ]  # 如果没有tag，用None表示默认分类

            for tag in feed_tags:
                tag_name = tag.name if tag else "uncategorized"

                # 获取或创建分类大纲
                if tag_name not in categories:
                    tag_outline = etree.SubElement(
                        body, "outline", text=tag_name, title=tag_name
                    )
                    categories[tag_name] = tag_outline
                else:
                    tag_outline = categories[tag_name]

                # 获取feed URL
                feed_url = get_feed_url_func(feed)

                # 添加feed条目
                etree.SubElement(
                    tag_outline,
                    "outline",
                    {
                        "title": feed.name,
                        "text": feed.name,
                        "type": "rss",
                        "xmlUrl": feed_url,
                        "htmlUrl": feed_url,
                    },
                )

        # 生成XML内容
        xml_content = etree.tostring(
            root, encoding="utf-8", xml_declaration=True, pretty_print=True
        )

        # 创建HTTP响应
        response = HttpResponse(xml_content, content_type="application/xml")
        response["Content-Disposition"] = (
            f'attachment; filename="{filename_prefix}_feeds_from_rsstranslator.opml"'
        )
        return response

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
        get_feed_url_func=lambda feed: f"{settings.SITE_URL}/feed/rss/{feed.slug}",
        filename_prefix="translated",
    )


@admin.display(description=_("Force update"))
def feed_force_update(modeladmin, request, queryset):
    logger.info("Call feed_force_update: %s", queryset)

    with transaction.atomic():
        for instance in queryset:
            instance.fetch_status = None
            instance.translation_status = None
            instance.save()

    feeds = queryset
    task_manager.submit_task("Force Update Feeds", update_multiple_feeds, feeds)


@admin.display(description=_("Recombine related feeds."))
def tag_force_update(modeladmin, request, queryset):
    logger.info("Call tag_force_update: %s", queryset)

    with transaction.atomic():
        for instance in queryset:
            task_manager.submit_task(
                "Force Update Tags", cache_tag, instance.slug, "t", "xml"
            )
            task_manager.submit_task(
                "Force Update Tags", cache_tag, instance.slug, "t", "json"
            )
            instance.last_updated = timezone.now()
            instance.save()


@admin.display(description=_("Batch modification"))
def feed_batch_modify(modeladmin, request, queryset):
    if "apply" in request.POST:
        logger.info("Apply feed_batch_modify")
        post_data = request.POST
        fields = {
            "update_frequency": "update_frequency_value",
            "max_posts": "max_posts_value",
            "translator": "translator_value",
            "target_language": "target_language_value",
            "translation_display": "translation_display_value",
            "summarizer": "summarizer_value",
            "summary_detail": "summary_detail_value",
            "additional_prompt": "additional_prompt_value",
            "fetch_article": "fetch_article",
            "tags": "tags_value",
            "translate_title": "translate_title",
            "translate_content": "translate_content",
            "summary": "summary",
            "filter": "filter_value",
        }
        field_types = {
            "update_frequency": int,
            "max_posts": int,
            "target_language": str,
            "translation_display": int,
            "summary_detail": float,
            "additional_prompt": str,
            "fetch_article": literal_eval,
            "translate_title": literal_eval,
            "translate_content": literal_eval,
            "summary": literal_eval,
        }
        translate_title = request.POST.get("translate_title", "Keep")
        translate_content = request.POST.get("translate_content", "Keep")
        summary = request.POST.get("summary", "Keep")

        match translate_title:
            case "Keep":
                pass
            case "True":
                queryset.update(translate_title=True)
            case "False":
                queryset.update(translate_title=False)

        match translate_content:
            case "Keep":
                pass
            case "True":
                queryset.update(translate_content=True)
            case "False":
                queryset.update(translate_content=False)

        match summary:
            case "Keep":
                pass
            case "True":
                queryset.update(summary=True)
            case "False":
                queryset.update(summary=False)

        update_fields = {}
        for field, value_field in fields.items():
            value = post_data.get(value_field)
            if post_data.get(field, "Keep") != "Keep" and value:
                match field:
                    case "translator":
                        content_type_id, object_id = map(int, value.split(":"))
                        queryset.update(translator_content_type_id=content_type_id)
                        queryset.update(translator_object_id=object_id)
                    case "summarizer":
                        content_type_summary_id, object_id_summary = map(
                            int, value.split(":")
                        )
                        queryset.update(
                            summarizer_content_type_id=content_type_summary_id
                        )
                        queryset.update(summarizer_object_id=object_id_summary)
                    case "tags":
                        tag_values = post_data.getlist(
                            "tags_value"
                        )  # 获取所有选中的 tag IDs（可能是多选）
                        if tag_values:
                            tag_ids = [int(id) for id in tag_values]  # 转换成整数列表
                            for feed in queryset:
                                feed.tags.set(tag_ids)  # 批量更新每个 Feed 的 tags
                    case "filter":
                        filter_values = post_data.getlist("filter_value")
                        if filter_values:
                            filter_ids = [int(id) for id in filter_values]
                            for obj in queryset:
                                obj.filters.set(filter_ids)
                    case _:
                        update_fields[field] = field_types.get(field, str)(value)

        if update_fields:
            queryset.update(**update_fields)
        return redirect(request.get_full_path())

    translator_choices = get_all_agent_choices()
    summary_engine_choices = get_ai_agent_choices()
    filter_choices = [(f"{filter.id}", filter.name) for filter in Filter.objects.all()]
    tags_choices = [(f"{tag.id}", tag.name) for tag in Tag.objects.all()]
    return render(
        request,
        "admin/feed_batch_modify.html",
        context={
            **core_admin_site.each_context(request),
            "items": queryset,
            "translator_choices": translator_choices,
            "target_language_choices": settings.TRANSLATION_LANGUAGES,
            "summary_engine_choices": summary_engine_choices,
            "filter_choices": filter_choices,
            "tags_choices": tags_choices,
            "update_frequency_choices": [
                (5, "5 min"),
                (15, "15 min"),
                (30, "30 min"),
                (60, "hourly"),
                (1440, "daily"),
                (10080, "weekly"),
            ],
        },
    )


# @admin.display(description=_("Create Digest"))
def create_digest(self, request, queryset):
    selected_ids = queryset.values_list("id", flat=True)
    ids_string = ",".join(str(id) for id in selected_ids)
    url = reverse("admin:core_digest_add")
    return HttpResponseRedirect(f"{url}?feed_ids={ids_string}")

import logging
from datetime import datetime
from ast import literal_eval
from django.contrib import admin
from django.shortcuts import render, redirect
from django.http import HttpResponse
from django.db import transaction
from django.core.cache import cache
from django.utils.translation import gettext_lazy as _
from django.conf import settings
from lxml import etree
from utils.modelAdmin_utils import get_translator_and_summary_choices
from .custom_admin_site import core_admin_site
from .models import Feed
from utils.task_manager import task_manager
from .management.commands.update_feeds import update_multiple_feeds


@admin.display(description=_("Clean translated content"))
def clean_translated_content(modeladmin, request, queryset):
    pass

@admin.display(description=_("Export selected original feeds as OPML"))
def export_original_feed_as_opml(modeladmin, request, queryset):
    try:
        # 创建根元素 <opml> 并设置版本
        root = etree.Element("opml", version="2.0")
        
        # 创建头部 <head>
        head = etree.SubElement(root, "head")
        etree.SubElement(head, "title").text = "Original Feeds | RSS Translator"
        etree.SubElement(head, "dateCreated").text = datetime.now().strftime("%a, %d %b %Y %H:%M:%S %z")
        etree.SubElement(head, "ownerName").text = "RSS Translator"
        
        # 创建主体 <body>
        body = etree.SubElement(root, "body")
        
        # 按分类组织订阅源
        categories = {}
        for item in queryset:
            category_name = item.category.name if item.category else "default"
            
            # 获取或创建分类大纲
            if category_name not in categories:
                category_outline = etree.Element("outline", text=category_name, title=category_name)
                categories[category_name] = category_outline
                body.append(category_outline)
            else:
                category_outline = categories[category_name]
            
            # 添加订阅源条目
            etree.SubElement(
                category_outline,
                "outline",
                {
                    "title": item.name,
                    "text": item.name,
                    "type": "rss",
                    "xmlUrl": item.feed_url,
                    "htmlUrl": item.feed_url
                }
            )
        
        # 生成 XML 内容
        xml_content = etree.tostring(
            root,
            encoding="utf-8",
            xml_declaration=True,
            pretty_print=True
        )
        
        # 创建 HTTP 响应
        response = HttpResponse(xml_content, content_type="application/xml")
        response["Content-Disposition"] = 'attachment; filename="original_feeds_from_rsstranslator.opml"'
        return response
        
    except Exception as e:
        logging.error("export_original_feed_as_opml: %s", str(e))
        return HttpResponse("An error occurred", status=500)

@admin.display(description=_("Export selected translated feeds as OPML"))
def export_translated_feed_as_opml(modeladmin, request, queryset):
    try:
        # 创建根元素 <opml> 并设置版本
        root = etree.Element("opml", version="2.0")
        
        # 创建头部 <head>
        head = etree.SubElement(root, "head")
        etree.SubElement(head, "title").text = "Translated Feeds | RSS Translator"
        etree.SubElement(head, "dateCreated").text = datetime.now().strftime("%a, %d %b %Y %H:%M:%S %z")
        etree.SubElement(head, "ownerName").text = "RSS Translator"
        
        # 创建主体 <body>
        body = etree.SubElement(root, "body")
        
        # 按分类组织订阅源
        categories = {}
        for item in queryset:
            category_name = item.category.name if item.category else "default"
            
            # 获取或创建分类大纲
            if category_name not in categories:
                category_outline = etree.Element("outline", text=category_name, title=category_name)
                categories[category_name] = category_outline
                body.append(category_outline)
            else:
                category_outline = categories[category_name]
            
            translated_feed_url = f"{settings.SITE_URL}/feed/rss/{item.slug}"
            
            # 添加订阅源条目
            etree.SubElement(
                category_outline,
                "outline",
                {
                    "title": item.name,
                    "text": item.name,
                    "type": "rss",
                    "xmlUrl": translated_feed_url,
                    "htmlUrl": translated_feed_url
                }
            )
        
        # 生成 XML 内容
        xml_content = etree.tostring(
            root,
            encoding="utf-8",
            xml_declaration=True,
            pretty_print=True
        )
        
        # 创建 HTTP 响应
        response = HttpResponse(xml_content, content_type="application/xml")
        response["Content-Disposition"] = 'attachment; filename="translated_feeds_from_rsstranslator.opml"'
        return response
        
    except Exception as e:
        logging.error("export_original_feed_as_opml: %s", str(e))
        return HttpResponse("An error occurred", status=500)

@admin.display(description=_("Force update"))
def feed_force_update(modeladmin, request, queryset):
    logging.info("Call feed_force_update: %s", queryset)

    with transaction.atomic():
        for instance in queryset:
            instance.fetch_status = None
            instance.translation_status = None
            instance.save()
    
    feeds = queryset
    task_manager.submit_task( 
            f"Force Update Feeds",
            update_multiple_feeds,
            feeds
        )

@admin.display(description=_("Batch modification"))
def feed_batch_modify(modeladmin, request, queryset):
    if "apply" in request.POST:
        logging.info("Apply feed_batch_modify")
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
            "quality": "quality",
            "category": "category_value",
            "translate_title": "translate_title",
            "translate_content": "translate_content",
            "summary": "summary",
        }
        field_types = {
            "update_frequency": int,
            "max_posts": int,
            "target_language": str,
            "translation_display": int,
            "summary_detail": float,
            "additional_prompt": str,
            "fetch_article": literal_eval,
            "quality": literal_eval,
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
                        queryset.update(summarizer_content_type_id=content_type_summary_id)
                        queryset.update(summarizer_object_id=object_id_summary)
                    case "category":
                        tag_model = Feed.category.tag_model
                        category_o, _ = tag_model.objects.get_or_create(name=value)
                        queryset.update(category=category_o)

                    case _:
                        update_fields[field] = field_types.get(field, str)(value)

        if update_fields:
            queryset.update(**update_fields)
        return redirect(request.get_full_path())

    translator_choices, summary_engine_choices = get_translator_and_summary_choices()
    logging.info(
        "translator_choices: %s, summary_engine_choices: %s",
        translator_choices,
        summary_engine_choices,
    )
    return render(
        request,
        "admin/feed_batch_modify.html",
        context={
            **core_admin_site.each_context(request),
            "items": queryset,
            "translator_choices": translator_choices,
            "target_language_choices": settings.TRANSLATION_LANGUAGES,
            "summary_engine_choices": summary_engine_choices,
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
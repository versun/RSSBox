# Manual created on 2025-05-27 16:10
from django.db import migrations
import uuid
from django.conf import settings
from utils.back_db import backup_db


def merge_feeds_data(apps, schema_editor):
    """
    将O_Feed和T_Feed的数据合并到Feed模型中
    """
    # 获取历史模型
    O_Feed = apps.get_model("core", "O_Feed")
    T_Feed = apps.get_model("core", "T_Feed")
    Feed = apps.get_model("core", "Feed")

    for o_feed in O_Feed.objects.all():
        t_feeds = T_Feed.objects.filter(o_feed=o_feed)
        if t_feeds.exists():
            for t_feed in t_feeds:
                # 创建新的Feed记录
                Feed.objects.create(
                    # 基本信息从O_Feed复制
                    name=o_feed.name,
                    feed_url=o_feed.feed_url,
                    translation_display=o_feed.translation_display,
                    etag=o_feed.etag,
                    fetch_status=o_feed.valid,  # O_Feed.valid -> Feed.fetch
                    update_frequency=o_feed.update_frequency,
                    max_posts=o_feed.max_posts,
                    quality=o_feed.quality,
                    fetch_article=o_feed.fetch_article,
                    summary_detail=o_feed.summary_detail,
                    additional_prompt=o_feed.additional_prompt,
                    category=o_feed.category,
                    # 翻译器和摘要引擎的GenericForeignKey
                    translator_content_type=o_feed.content_type,
                    translator_object_id=o_feed.object_id,
                    summarizer_content_type=o_feed.content_type_summary,
                    summarizer_object_id=o_feed.object_id_summary,
                    # 语言和翻译相关信息从T_Feed复制
                    target_language=t_feed.language,
                    translation_status=t_feed.status,
                    translate_title=t_feed.translate_title,
                    translate_content=t_feed.translate_content,
                    summary=t_feed.summary,
                    total_tokens=t_feed.total_tokens,
                    total_characters=t_feed.total_characters,
                    # 时间戳
                    last_translate=None,
                    last_fetch=o_feed.last_pull,
                    # URL slug
                    slug=t_feed.sid if t_feed.sid else None,
                )

        else:
            Feed.objects.create(
                # 基本信息从O_Feed复制
                name=o_feed.name,
                feed_url=o_feed.feed_url,
                translation_display=o_feed.translation_display,
                etag=o_feed.etag,
                fetch_status=o_feed.valid,  # O_Feed.valid -> Feed.fetch
                update_frequency=o_feed.update_frequency,
                max_posts=o_feed.max_posts,
                quality=o_feed.quality,
                fetch_article=o_feed.fetch_article,
                summary_detail=o_feed.summary_detail,
                additional_prompt=o_feed.additional_prompt,
                category=o_feed.category,
                # 翻译器和摘要引擎的GenericForeignKey
                translator_content_type=o_feed.content_type,
                translator_object_id=o_feed.object_id,
                summarizer_content_type=o_feed.content_type_summary,
                summarizer_object_id=o_feed.object_id_summary,
                # 语言和翻译相关信息为空
                target_language=settings.DEFAULT_TARGET_LANGUAGE,
                translation_status=None,
                translate_title=False,
                translate_content=False,
                summary=False,
                total_tokens=0,
                total_characters=0,
                # 时间戳
                last_translate=None,
                last_fetch=o_feed.last_pull,
                # URL slug
                slug=o_feed.sid if o_feed.sid else None,  # 没有T_Feed时，slug为None
            )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0017_alter_o_feed_content_type_tagulous_feed_category_and_more"),
    ]

    operations = [
        migrations.RunPython(backup_db),
        migrations.RunPython(
            merge_feeds_data,
            migrations.RunPython.noop,
        ),
    ]

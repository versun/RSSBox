from django.db import transaction
from django.utils import timezone


def force_update_feeds(
    queryset,
    *,
    task_manager,
    update_multiple_feeds_func,
):
    with transaction.atomic():
        for instance in queryset:
            instance.fetch_status = None
            instance.translation_status = None
            instance.save()

    task_manager.submit_task(
        "Force Update Feeds",
        update_multiple_feeds_func,
        queryset,
    )


def force_update_tags(
    queryset,
    *,
    task_manager,
    cache_tag_func,
    now_func=timezone.now,
):
    with transaction.atomic():
        for instance in queryset:
            task_manager.submit_task(
                "Force Update Tags", cache_tag_func, instance.slug, "t", "xml"
            )
            task_manager.submit_task(
                "Force Update Tags", cache_tag_func, instance.slug, "t", "json"
            )
            instance.last_updated = now_func()
            instance.save()

import json

from django.db import models
from utils import text_handler


def needs_re_evaluation(result, entry):
    if result.passed is None:
        return True

    if entry.updated and entry.updated > result.last_updated:
        return True

    return False


def apply_keywords_filter(filter_obj, queryset):
    keywords = filter_obj.keywords.values_list("name", flat=True)

    if not keywords:
        return queryset.none() if filter_obj.operation == filter_obj.INCLUDE else queryset

    query = models.Q()
    for keyword in keywords:
        if filter_obj.filter_original_title:
            query |= models.Q(original_title__icontains=keyword)
        if filter_obj.filter_original_content:
            query |= models.Q(original_content__icontains=keyword)
        if filter_obj.filter_translated_title:
            query |= models.Q(translated_title__icontains=keyword)
        if filter_obj.filter_translated_content:
            query |= models.Q(translated_content__icontains=keyword)

    if filter_obj.operation == filter_obj.INCLUDE:
        return queryset.filter(query).distinct()
    return queryset.exclude(query).distinct()


def apply_ai_filter(filter_obj, queryset):
    from core.models.filter import FilterResult

    passed_ids = []
    tokens = 0
    for entry in queryset:
        result, created = FilterResult.objects.get_or_create(
            filter=filter_obj,
            entry=entry,
        )

        if created or needs_re_evaluation(result, entry):
            json_data = {}
            if filter_obj.filter_original_title:
                json_data["original_title"] = entry.original_title
            if filter_obj.filter_original_content:
                json_data["original_content"] = text_handler.clean_content(
                    entry.original_content
                )
            if filter_obj.filter_translated_title:
                json_data["translated_title"] = entry.translated_title
            if filter_obj.filter_translated_content:
                json_data["translated_content"] = text_handler.clean_content(
                    entry.translated_content
                )

            text_str = json.dumps(json_data, ensure_ascii=False)
            passed = None
            if filter_obj.agent:
                filter_results = filter_obj.agent.filter(
                    text=text_str,
                    system_prompt=filter_obj.filter_prompt,
                )
                passed = filter_results["passed"]
                tokens += filter_results["tokens"]
            result.passed = passed
            result.save()
        else:
            passed = result.passed

        if passed:
            passed_ids.append(entry.id)

    return queryset.filter(id__in=passed_ids), tokens


def apply_filter(filter_obj, queryset):
    tokens = 0
    if filter_obj.filter_method in [filter_obj.KEYWORD_ONLY, filter_obj.BOTH]:
        queryset = apply_keywords_filter(filter_obj, queryset)

    if filter_obj.filter_method in [filter_obj.AI_ONLY, filter_obj.BOTH] and filter_obj.agent:
        queryset, tokens = apply_ai_filter(filter_obj, queryset)

    if tokens > 0:
        filter_obj.total_tokens += tokens
        filter_obj.save()

    return queryset


def apply_feed_filters(feed, queryset):
    for filter_obj in feed.filters.all():
        queryset = apply_filter(filter_obj, queryset)
    return queryset


def apply_tag_filters(tag_slug, entry_ids, all_entries):
    from core.models.entry import Entry
    from core.models.tag import Tag

    tag_filters = Tag.objects.get(slug=tag_slug).filters.all()

    if not tag_filters:
        return [entry for (_, entry) in all_entries]

    filtered_qs = Entry.objects.filter(id__in=entry_ids)
    for filter_obj in tag_filters:
        filtered_qs = apply_filter(filter_obj, filtered_qs)

    passed_ids = set(filtered_qs.values_list("id", flat=True))
    return [entry for (_, entry) in all_entries if entry.id in passed_ids]

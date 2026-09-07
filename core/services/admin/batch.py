from ast import literal_eval


def apply_batch_updates(queryset, post_data):
    getlist = (
        post_data.getlist
        if hasattr(post_data, "getlist")
        else lambda key: post_data.get(key, [])
    )

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

    translate_title = post_data.get("translate_title", "Keep")
    translate_content = post_data.get("translate_content", "Keep")
    summary = post_data.get("summary", "Keep")

    match translate_title:
        case "True":
            queryset.update(translate_title=True)
        case "False":
            queryset.update(translate_title=False)

    match translate_content:
        case "True":
            queryset.update(translate_content=True)
        case "False":
            queryset.update(translate_content=False)

    match summary:
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
                    queryset.update(summarizer_id=int(value))
                case "tags":
                    tag_values = getlist("tags_value")
                    if tag_values:
                        tag_ids = [int(current_id) for current_id in tag_values]
                        for feed in queryset:
                            feed.tags.set(tag_ids)
                case "filter":
                    filter_values = getlist("filter_value")
                    if filter_values:
                        filter_ids = [int(current_id) for current_id in filter_values]
                        for obj in queryset:
                            obj.filters.set(filter_ids)
                case _:
                    update_fields[field] = field_types.get(field, str)(value)

    if update_fields:
        queryset.update(**update_fields)


def build_batch_modify_context(
    queryset,
    *,
    get_all_agent_choices_func,
    openai_agent_model,
    filter_model,
    tag_model,
    settings_module,
    admin_context,
):
    translator_choices = get_all_agent_choices_func()
    summary_engine_choices = [
        (str(agent.id), agent.name)
        for agent in openai_agent_model.objects.filter(valid=True)
    ]
    filter_choices = [
        (f"{filter_obj.id}", filter_obj.name)
        for filter_obj in filter_model.objects.all()
    ]
    tags_choices = [
        (f"{tag.id}", tag.name)
        for tag in tag_model.objects.all()
    ]

    return {
        **admin_context,
        "items": queryset,
        "translator_choices": translator_choices,
        "target_language_choices": settings_module.TRANSLATION_LANGUAGES,
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
    }

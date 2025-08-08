from django.utils.html import format_html
from django.contrib.contenttypes.models import ContentType
from core.models import OpenAIAgent, DeepLAgent, LibreTranslateAgent, TestAgent


AGENT_MODELS = [OpenAIAgent, DeepLAgent, LibreTranslateAgent, TestAgent]


def get_all_agent_choices():
    """
    获取所有代理选择项，包括翻译器和摘要引擎
    :return: 包含翻译器和摘要引擎的选择项
    """
    content_types = {
        model: ContentType.objects.get_for_model(model) for model in AGENT_MODELS
    }

    # Build all choices in one list comprehension
    agent_choices = [
        (f"{content_types[model].id}:{obj_id}", obj_name)
        for model in AGENT_MODELS
        for obj_id, obj_name in model.objects.filter(valid=True).values_list(
            "id", "name"
        )
    ]
    return agent_choices


def get_ai_agent_choices():
    """
    获取所有AI代理选择项
    :return: 包含AI代理的选择项
    """
    content_types = {
        model: ContentType.objects.get_for_model(model) for model in AGENT_MODELS
    }

    # Build all choices in one list comprehension
    ai_agent_choices = [
        (f"{content_types[model].id}:{obj_id}", obj_name)
        for model in AGENT_MODELS
        for obj_id, obj_name in model.objects.filter(
            valid=True, is_ai=True
        ).values_list("id", "name")
    ]
    return ai_agent_choices


def status_icon(status):
    match status:
        case None:
            return format_html(
                "<img src='/static/img/icon-loading.svg' alt='In Progress'>"
            )
        case True:
            return format_html(
                "<img src='/static/admin/img/icon-yes.svg' alt='Succeed'>"
            )
        case False:
            return format_html("<img src='/static/admin/img/icon-no.svg' alt='Error'>")

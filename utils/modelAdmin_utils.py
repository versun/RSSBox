from django.conf import settings
from django.utils.html import format_html
from django.apps import apps
from django.utils.translation import gettext_lazy as _
from django.contrib.contenttypes.models import ContentType

# if settings.DEBUG:
#    from huey_monitor.models import TaskModel


# def get_all_subclasses(cls):
#     subclasses = set()
#     for subclass in cls.__subclasses__():
#         if not subclass.__subclasses__():
#             subclasses.add(subclass)
#         subclasses.update(get_all_subclasses(subclass))
#     return subclasses
def get_all_app_models(app_name):
    app = apps.get_app_config(app_name)
    models = app.get_models()
    # exclude Translated_Content
    exclude_models = []
    if not settings.DEBUG:
        exclude_models.append("TestTranslator")

    models = [model for model in models if model.__name__ not in exclude_models]

    return models


def get_translator_and_summary_choices():
    translator_models = get_all_app_models("translator")
    # Cache ContentTypes to avoid repetitive database calls
    content_types = {
        model: ContentType.objects.get_for_model(model) for model in translator_models
    }

    # Build all choices in one list comprehension
    translator_choices = [
        (f"{content_types[model].id}:{obj_id}", obj_name)
        for model in translator_models
        for obj_id, obj_name in model.objects.filter(valid=True).values_list(
            "id", "name"
        )
    ]

    summary_engine_choices = [
        (f"{content_types[model].id}:{obj_id}", obj_name)
        for model in translator_models
        for obj_id, obj_name in model.objects.filter(
            valid=True, is_ai=True
        ).values_list("id", "name")
    ]
    return translator_choices, summary_engine_choices


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

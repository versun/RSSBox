import logging
from django.contrib import admin
from core.admin import core_admin_site

from django.conf import settings
from django.shortcuts import redirect
from .models import *

# from django.utils.translation import gettext_lazy  as _

from utils.modelAdmin_utils import status_icon


class BaseTranslatorAdmin(admin.ModelAdmin):
    get_model_perms = lambda self, request: {}  # 不显示在admin页面

    def save_model(self, request, obj, form, change):
        logging.info("Call save_model: %s", obj)
        # obj.valid = None
        # obj.save()
        try:
            obj.valid = obj.validate()
        except Exception as e:
            obj.valid = False
            logging.error("Error in translator: %s", e)
        finally:
            obj.save()
        return redirect("/translator")

    def is_valid(self, obj):
        return status_icon(obj.valid)

    is_valid.short_description = "Valid"

    def masked_api_key(self, obj):
        api_key = obj.api_key if hasattr(obj, "api_key") else obj.token
        if api_key:
            return f"{api_key[:3]}...{api_key[-3:]}"
        return ""

    masked_api_key.short_description = "API Key"

    def delete_model(self, request, obj):
        super().delete_model(request, obj)
        # 重定向到指定URL
        return redirect("/translator")


class OpenAITranslatorAdmin(BaseTranslatorAdmin):
    fields = [
        "name",
        "api_key",
        "base_url",
        "model",
        "translate_prompt",
        "content_translate_prompt",
        "summary_prompt",
        "temperature",
        "top_p",
        "frequency_penalty",
        "presence_penalty",
        "max_tokens",
        "rate_limit_rpm",
    ]
    list_display = [
        "name",
        "is_valid",
        "masked_api_key",
        "model",
        "translate_prompt",
        "content_translate_prompt",
        "summary_prompt",
        "max_tokens",
        "base_url",
    ]


class DeepLTranslatorAdmin(BaseTranslatorAdmin):
    fields = ["name", "api_key", "server_url", "proxy", "max_characters"]
    list_display = [
        "name",
        "is_valid",
        "masked_api_key",
        "server_url",
        "proxy",
        "max_characters",
    ]


class TestTranslatorAdmin(BaseTranslatorAdmin):
    fields = ["name", "translated_text", "max_characters", "max_tokens", "interval"]
    list_display = ["name", "is_valid", "translated_text", "max_characters", "max_tokens", "interval"]


core_admin_site.register(OpenAITranslator, OpenAITranslatorAdmin)
core_admin_site.register(DeepLTranslator, DeepLTranslatorAdmin)

if settings.DEBUG:
    core_admin_site.register(TestTranslator, TestTranslatorAdmin)

import logging
from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from django.conf import settings
from django.shortcuts import redirect
from core.models.agent import OpenAIAgent, DeepLAgent, LibreTranslateAgent, TestAgent
from utils.modelAdmin_utils import status_icon
from core.admin import core_admin_site


class AgentAdmin(admin.ModelAdmin):
    # get_model_perms = lambda self, request: {}  # 不显示在admin页面

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
        return redirect("/core/agent")

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
        return redirect("/core/agent")


class OpenAIAgentAdmin(AgentAdmin):
    change_form_template = "admin/change_form_with_tabs.html"
    list_display = [
        "name",
        "is_valid",
        "masked_api_key",
        "model",
        "title_translate_prompt",
        "content_translate_prompt",
        "summary_prompt",
        "max_tokens",
        "base_url",
    ]
    fieldsets = (
        (
            _("Model Information"),
            {
                "fields": (
                    "name",
                    "api_key",
                    "base_url",
                    "model",
                )
            },
        ),
        (
            _("Prompts"),
            {
                "fields": (
                    "title_translate_prompt",
                    "content_translate_prompt",
                    "summary_prompt",
                )
            },
        ),
        (
            _("Advanced"),
            {
                "fields": (
                    "temperature",
                    "top_p",
                    "frequency_penalty",
                    "presence_penalty",
                    "max_tokens",
                    "rate_limit_rpm",
                )
            },
        ),
    )


class DeepLAgentAdmin(AgentAdmin):
    fields = ["name", "api_key", "server_url", "proxy", "max_characters"]
    list_display = [
        "name",
        "is_valid",
        "masked_api_key",
        "server_url",
        "proxy",
        "max_characters",
    ]


class LibreTranslateAgentAdmin(AgentAdmin):  
    fields = ["name", "api_key", "server_url", "max_characters"]
    list_display = [
        "name",
        "is_valid",
        "masked_api_key",
        "server_url",
        "max_characters",
    ]

class TestAgentAdmin(AgentAdmin):
    fields = ["name", "translated_text", "max_characters", "max_tokens", "interval"]
    list_display = [
        "name",
        "is_valid",
        "translated_text",
        "max_characters",
        "max_tokens",
        "interval",
    ]


core_admin_site.register(OpenAIAgent, OpenAIAgentAdmin)
core_admin_site.register(DeepLAgent, DeepLAgentAdmin)
core_admin_site.register(LibreTranslateAgent, LibreTranslateAgentAdmin)

if settings.DEBUG:
    core_admin_site.register(TestAgent, TestAgentAdmin)

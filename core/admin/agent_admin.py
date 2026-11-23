import logging
from django.apps import apps
from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from django.conf import settings
from django.utils.html import format_html, mark_safe
from django.shortcuts import redirect
from django.db import transaction, close_old_connections
from core.models.agent import OpenAIAgent, DeepLAgent, LibreTranslateAgent, TestAgent
from core.forms import OpenAIAgentForm
from utils.modelAdmin_utils import status_icon
from core.admin import core_admin_site
from core.tasks.task_manager import task_manager

logger = logging.getLogger(__name__)


def validate_agent_by_id(model_label: str, agent_id: int):
    """
    Validate agent in worker thread by reloading from DB.
    Avoid sharing request-thread model instances with background threads.
    """
    close_old_connections()
    try:
        model = apps.get_model(model_label)
        if model is None:
            logger.error("Unknown model label for validation task: %s", model_label)
            return False

        agent = model.objects.filter(pk=agent_id).first()
        if agent is None:
            logger.warning(
                "Skip validate task because agent not found: %s#%s",
                model_label,
                agent_id,
            )
            return False

        return agent.validate()
    finally:
        close_old_connections()


class AgentAdmin(admin.ModelAdmin):
    # get_model_perms = lambda self, request: {}  # 不显示在admin页面
    def save_model(self, request, obj, form, change):
        logger.info("Call save_model: %s", obj)

        try:
            obj.valid = None
            obj.save()

            model_label = obj._meta.label_lower
            task_name = f"validate_agent_{obj.id}"
            agent_id = obj.id

            def submit_validation_task():
                try:
                    task_manager.submit_task(
                        task_name, validate_agent_by_id, model_label, agent_id
                    )
                except Exception as e:
                    logger.error("Error in agent: %s", e)
                    obj.__class__.objects.filter(pk=agent_id).update(valid=False)

            transaction.on_commit(submit_validation_task)
        except Exception as e:
            obj.valid = False
            logger.error("Error in agent: %s", e)
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

    @admin.display(description=_("Log"))
    def show_log(self, obj):
        return format_html(
            """
            <details>
                <summary>show</summary>
                <div style="max-height: 200px; overflow: auto;">
                    {0}
                </div>
            </details>
            """,
            mark_safe(obj.log),
        )


class OpenAIAgentAdmin(AgentAdmin):
    form = OpenAIAgentForm
    change_form_template = "admin/change_form_with_tabs.html"
    list_display = [
        "name",
        "is_valid",
        "masked_api_key",
        "model",
        "show_max_tokens",
        "base_url",
    ]
    readonly_fields = ["show_log", "show_max_tokens"]
    fieldsets = (
        (
            _("Model Information"),
            {"fields": ("name", "api_key", "base_url", "model", "show_log")},
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
                    "advanced_params",
                    "custom_headers",
                    "rate_limit_rpm",
                    # "show_max_tokens",
                    "max_tokens",
                    "max_chunk_tokens",
                    "request_timeout",
                    "merge_system_prompt",
                )
            },
        ),
    )

    @admin.display(description=_("Max Tokens"))
    def show_max_tokens(self, obj):
        if obj.max_tokens == 0:
            return "Detecting..."
        return obj.max_tokens


class DeepLAgentAdmin(AgentAdmin):
    fields = ["name", "api_key", "server_url", "proxy", "max_characters", "show_log"]
    list_display = [
        "name",
        "is_valid",
        "masked_api_key",
        "server_url",
        "proxy",
        "max_characters",
    ]
    readonly_fields = ["show_log"]


class LibreTranslateAgentAdmin(AgentAdmin):
    fields = ["name", "api_key", "server_url", "max_characters", "show_log"]
    list_display = [
        "name",
        "is_valid",
        "masked_api_key",
        "server_url",
        "max_characters",
    ]
    readonly_fields = ["show_log"]


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
    readonly_fields = ["show_log"]


core_admin_site.register(OpenAIAgent, OpenAIAgentAdmin)
core_admin_site.register(DeepLAgent, DeepLAgentAdmin)
core_admin_site.register(LibreTranslateAgent, LibreTranslateAgentAdmin)

if settings.DEBUG:
    core_admin_site.register(TestAgent, TestAgentAdmin)

from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _

from core.admin import core_admin_site
from core.models import RuntimeConfiguration
from core.runtime_policy import get_runtime_policy


@admin.register(RuntimeConfiguration, site=core_admin_site)
class RuntimeConfigurationAdmin(admin.ModelAdmin):
    fields = (
        "effective_defaults", "request_timeout", "max_attempts",
        "retry_base_seconds", "retry_max_seconds", "attention_after_attempts",
        "updated_at",
    )
    readonly_fields = ("effective_defaults", "updated_at")

    @admin.display(description=_("Currently effective settings"))
    def effective_defaults(self, obj):
        policy = get_runtime_policy()
        return _("Timeout: %(timeout)s s; maximum failed rounds: %(attempts)s; retry delay: %(base)s s, doubling up to %(cap)s s.") % {
            "timeout": policy.request_timeout, "attempts": policy.max_attempts,
            "base": policy.retry_base_seconds, "cap": policy.retry_max_seconds,
        }

    def has_add_permission(self, request):
        return super().has_add_permission(request) and not RuntimeConfiguration.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        obj.pk = 1
        super().save_model(request, obj, form, change)
        self.message_user(request, _("Saved. Applies to new requests and future failure scheduling; running requests and existing retry timestamps are unchanged."), messages.INFO)

"""Optional database overrides; environment values remain the fallback."""
import math

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _


class RuntimeConfiguration(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    request_timeout = models.FloatField(
        _("Default request timeout (seconds)"), null=True, blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(86400)],
        help_text=_("Leave blank to inherit OPENAI_API_TIMEOUT. Per-agent overrides take precedence."),
    )
    max_attempts = models.PositiveSmallIntegerField(
        _("Maximum failed rounds before automatic pause"), null=True, blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(100)],
        help_text=_("Includes the initial failed round; leave blank to inherit ENTRY_MAX_RETRY_ATTEMPTS."),
    )
    retry_base_seconds = models.PositiveIntegerField(
        _("Initial retry delay (seconds)"), null=True, blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(604800)],
        help_text=_("Delay doubles after each failed round; leave blank to inherit ENTRY_RETRY_BASE_DELAY_SECONDS."),
    )
    retry_max_seconds = models.PositiveIntegerField(
        _("Maximum retry delay (seconds)"), null=True, blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(604800)],
        help_text=_("Leave blank to inherit ENTRY_RETRY_MAX_DELAY_SECONDS."),
    )
    attention_after_attempts = models.PositiveSmallIntegerField(
        _("Show failures in attention view after this many failed rounds"), default=1,
        validators=[MinValueValidator(1), MaxValueValidator(100)],
        help_text=_("All failures are recorded immediately. Paused and pending tasks are always visible; this only filters the attention view."),
    )
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        super().clean()
        if self.pk != 1:
            raise ValidationError(_("Only one global AI configuration is allowed."))
        if self.request_timeout is not None and not math.isfinite(self.request_timeout):
            raise ValidationError({"request_timeout": _("Timeout must be finite.")})
        base = self.retry_base_seconds if self.retry_base_seconds is not None else settings.ENTRY_RETRY_BASE_DELAY_SECONDS
        cap = self.retry_max_seconds if self.retry_max_seconds is not None else settings.ENTRY_RETRY_MAX_DELAY_SECONDS
        if cap < base:
            raise ValidationError({"retry_max_seconds": _("Maximum retry delay must not be less than the initial delay.")})

    def __str__(self):
        return str(_("Global AI settings"))

    class Meta:
        verbose_name = _("Global AI settings")
        verbose_name_plural = _("Global AI settings")
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name="runtime_configuration_singleton")]

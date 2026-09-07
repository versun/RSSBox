import logging
from django.db import models
from django.utils.translation import gettext_lazy as _
from tagulous.models import TagField
from config import settings
from core.services.feed.filters import (
    apply_ai_filter as service_apply_ai_filter,
    apply_filter as service_apply_filter,
    apply_keywords_filter as service_apply_keywords_filter,
    needs_re_evaluation as service_needs_re_evaluation,
)

logger = logging.getLogger(__name__)


class Filter(models.Model):
    INCLUDE = True
    EXCLUDE = False
    OPERATION_CHOICES = (
        (INCLUDE, _("Include - Only show items containing these keywords")),
        (EXCLUDE, _("Exclude - Hide items containing these keywords")),
    )
    KEYWORD_ONLY = 0
    AI_ONLY = 1
    BOTH = 2
    FILTER_METHOD_CHOICES = (
        (KEYWORD_ONLY, _("Keyword Only")),
        (AI_ONLY, _("AI Only")),
        (BOTH, _("Both Keyword and AI (First Keyword, then AI)")),
    )

    name = models.CharField(
        _("Name"),
        max_length=255,
        blank=True,
        null=True,
    )
    keywords = TagField(
        verbose_name=_("Keywords"),
        blank=True,
        help_text=_("Keywords to filter entries. "),
    )

    agent = models.ForeignKey(
        "OpenAIAgent",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        default=None,
        related_name="filters",
        verbose_name=_("AI Agent"),
        help_text=_("Select a valid OpenAI agent for filtering"),
    )
    filter_prompt = models.TextField(
        _("Filter Prompt"),
        blank=True,
        null=True,
        default=settings.default_filter_prompt,
    )

    filter_method = models.PositiveSmallIntegerField(
        _("Filter Method"),
        choices=FILTER_METHOD_CHOICES,
        default=KEYWORD_ONLY,
        help_text=_("Choose which filtering method to apply"),
    )
    operation = models.BooleanField(
        choices=OPERATION_CHOICES,
        default=EXCLUDE,
        help_text=_("Action to take on matching keywords."),
    )

    filter_original_title = models.BooleanField(
        default=True,
        help_text="Apply filter to the original title of the entry.",
    )
    filter_original_content = models.BooleanField(
        default=True,
        help_text="Apply filter to the content of the entry.",
    )
    filter_translated_title = models.BooleanField(
        default=False,
        help_text="Apply filter to the translated title of the entry.",
    )
    filter_translated_content = models.BooleanField(
        default=False,
        help_text="Apply filter to the translated content of the entry.",
    )
    total_tokens = models.PositiveIntegerField(_("Tokens Cost"), default=0)

    def __str__(self):
        return f"{self.name}"

    class Meta:
        verbose_name = _("Filter")
        verbose_name_plural = _("Filter")

    def apply_keywords_filter(self, queryset):
        return service_apply_keywords_filter(self, queryset)

    def apply_ai_filter(self, queryset):
        return service_apply_ai_filter(self, queryset)

    def apply_filter(self, queryset):
        return service_apply_filter(self, queryset)

    def needs_re_evaluation(self, result, entry):
        return service_needs_re_evaluation(result, entry)

    def save(self, *args, **kwargs):
        """
        当关键配置变化时清除缓存结果
        """
        # 检查是否是新建对象
        is_new = self._state.adding

        # 如果不是新对象，获取数据库中的原始值
        original = None
        if not is_new:
            original = Filter.objects.get(pk=self.pk)

        # 调用父类保存方法
        super().save(*args, **kwargs)

        # 如果不是新对象且关键字段发生变化，清除缓存
        if not is_new and original is not None:
            # 检查关键字段是否变化
            ai_fields = [
                "agent_id",
                "filter_prompt",
                "filter_method",
                "filter_original_title",
                "filter_original_content",
                "filter_translated_title",
                "filter_translated_content",
            ]

            ai_fields_changed = any(
                getattr(original, field) != getattr(self, field) for field in ai_fields
            ) and self.filter_method in [self.AI_ONLY, self.BOTH]

            need_clear_ai_filter_cache = (
                self.filter_method in [self.AI_ONLY, self.BOTH] and ai_fields_changed
            )
            # 如果有变化，清除所有相关缓存结果
            if need_clear_ai_filter_cache:
                self.clear_ai_filter_cache_results()

    def clear_ai_filter_cache_results(self):
        """
        清除与此过滤器相关的所有缓存结果
        """
        FilterResult.objects.filter(filter=self).delete()
        logger.debug(f"Cleared cache for filter {self.name}")


class FilterResult(models.Model):
    filter = models.ForeignKey(Filter, on_delete=models.CASCADE, related_name="results")
    entry = models.ForeignKey(
        "Entry", on_delete=models.CASCADE, related_name="filter_results"
    )
    passed = models.BooleanField(
        _("Passed Filter"), blank=True, default=None, null=True
    )  # 是否通过过滤
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("filter", "entry")]
        indexes = [models.Index(fields=["filter", "entry", "passed"])]

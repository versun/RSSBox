import logging
from django.db import models
from django.core.cache import cache
from django.utils.translation import gettext_lazy as _
from tagulous.models import TagField
from utils import text_handler
import json
from config import settings

logger = logging.getLogger(__name__)

# How long a failed/pending AI filter decision is trusted before retrying.
# Prevents every request (or every cache miss) from calling the provider again.
AI_FILTER_RETRY_SECONDS = 300


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
        """
        应用过滤器到查询集，检查文本内容是否包含标签关键词
        :param queryset: 要过滤的查询集
        :return: 过滤后的查询集
        """
        keywords = self.keywords.values_list("name", flat=True)

        if not keywords:
            return queryset.none() if self.operation == self.INCLUDE else queryset

        # 构建查询条件：内容包含任何关键词
        query = models.Q()
        for keyword in keywords:
            if self.filter_original_title:
                query |= models.Q(original_title__icontains=keyword)
            if self.filter_original_content:
                query |= models.Q(original_content__icontains=keyword)
            if self.filter_translated_title:
                query |= models.Q(translated_title__icontains=keyword)
            if self.filter_translated_content:
                query |= models.Q(translated_content__icontains=keyword)

        if self.operation == self.INCLUDE:
            # 包含模式：只显示包含任何关键词的内容
            return queryset.filter(query).distinct()
        else:
            # 排除模式：隐藏包含任何关键词的内容
            return queryset.exclude(query).distinct()

    def _evaluate_with_ai(self, entry, result):
        """Ask the AI about one entry; on failure keep the previous decision.

        A short-lived marker per FilterResult version suppresses further provider
        calls while an evaluation is running or after it failed, so feed cache
        misses cannot trigger a retry storm. A successful save bumps
        last_updated, so the marker never needs to be deleted.
        """
        marker = f"ai_filter_retry_{result.pk}_{result.last_updated.timestamp()}"
        if not cache.add(marker, 1, timeout=AI_FILTER_RETRY_SECONDS):
            return result.passed, 0

        try:
            # 缺失字段按空字符串处理，单个坏条目不能拖垮整个 feed
            json_data = {}
            if self.filter_original_title:
                json_data["original_title"] = entry.original_title or ""
            if self.filter_original_content:
                json_data["original_content"] = text_handler.clean_content(
                    entry.original_content
                )
            if self.filter_translated_title:
                json_data["translated_title"] = entry.translated_title or ""
            if self.filter_translated_content:
                json_data["translated_content"] = text_handler.clean_content(
                    entry.translated_content
                )
            filter_results = self.agent.filter(
                text=json.dumps(json_data, ensure_ascii=False),
                # filter_prompt is nullable; the agent expects a string.
                system_prompt=self.filter_prompt or settings.default_filter_prompt,
            )
            passed = filter_results["passed"]
            tokens = filter_results["tokens"]
        except Exception as exc:
            logger.warning(
                "AI filter %s failed for entry %s; keeping previous decision %s: %s",
                self.pk, entry.pk, result.passed, exc,
            )
            return result.passed, 0

        result.passed = passed
        result.save(update_fields=["passed", "last_updated"])
        return passed, tokens

    def apply_ai_filter(self, queryset):
        """
        应用AI过滤器到查询集，使用AI代理处理内容
        :param queryset: 要过滤的查询集
        :return: 过滤后的查询集
        """
        if not self.agent_id:
            # No agent configured: fail open like an AI failure instead of
            # silently hiding every entry behind an unconfigured filter.
            return queryset, 0

        passed_ids = []
        tokens = 0
        for entry in queryset:
            # 尝试获取缓存结果
            result, _ = FilterResult.objects.get_or_create(filter=self, entry=entry)
            passed = result.passed

            # 新建的结果 passed 为 None，同样会触发评估
            if self.needs_re_evaluation(result, entry):
                passed, used_tokens = self._evaluate_with_ai(entry, result)
                tokens += used_tokens

            # Undecided entries (evaluation pending or failed) fail open;
            # only an explicit AI rejection hides an entry.
            if passed is not False:
                passed_ids.append(entry.id)

        # 过滤出通过的项目
        return queryset.filter(id__in=passed_ids), tokens

    def apply_filter(self, queryset):
        tokens = 0
        # 优先尝试使用关键字过滤
        if self.filter_method in [self.KEYWORD_ONLY, self.BOTH]:
            queryset = self.apply_keywords_filter(queryset)

        # 检查是否需要AI过滤
        if self.filter_method in [self.AI_ONLY, self.BOTH] and self.agent_id:
            queryset, tokens = self.apply_ai_filter(queryset)

        if tokens > 0:
            # Persist only the counter so a concurrent admin edit of e.g. the
            # prompt or method is not clobbered by a full-object save.
            Filter.objects.filter(pk=self.pk).update(
                total_tokens=models.F("total_tokens") + tokens
            )
            self.total_tokens += tokens

        return queryset

    def needs_re_evaluation(self, result, entry):
        """未评估过，或条目内容在上次评估后更新过"""
        return result.passed is None or bool(
            entry.updated and entry.updated > result.last_updated
        )

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

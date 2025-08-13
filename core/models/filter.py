import logging
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone
from tagulous.models import TagField
from utils import text_handler
import json
from config import settings


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

    agent_content_type = models.ForeignKey(
        ContentType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        default=None,
        related_name="filter_agent",
    )
    agent_object_id = models.PositiveIntegerField(null=True, blank=True, default=None)
    agent = GenericForeignKey("agent_content_type", "agent_object_id")
    filter_prompt = models.TextField(_("Filter Prompt"), blank=True, null=True, default=settings.default_filter_prompt)

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

    def apply_ai_filter(self, queryset):
        """
        应用AI过滤器到查询集，使用AI代理处理内容
        :param queryset: 要过滤的查询集
        :return: 过滤后的查询集
        """
        passed_ids = []
        tokens = 0
        for entry in queryset:
            # 尝试获取缓存结果
            result, created = FilterResult.objects.get_or_create(
                filter=self,
                entry=entry,
            )

            # 检查是否需要重新评估
            if created or self.needs_re_evaluation(result, entry):
                # 准备要发送给AI的内容
                json_data = {}
                if self.filter_original_title:
                    json_data["original_title"] = entry.original_title
                if self.filter_original_content:
                    json_data["original_content"] = text_handler.clean_content(
                        entry.original_content
                    )
                if self.filter_translated_title:
                    json_data["translated_title"] = entry.translated_title
                if self.filter_translated_content:
                    json_data["translated_content"] = text_handler.clean_content(
                        entry.translated_content
                    )

                text_str = json.dumps(json_data, ensure_ascii=False)
                filter_results = self.agent.filter(
                    text=text_str, system_prompt=self.filter_prompt
                )
                passed = filter_results["passed"]
                tokens += filter_results["tokens"]
                result.passed = passed
                result.save()
            else:
                passed = result.passed

            if passed:
                passed_ids.append(entry.id)

        # 过滤出通过的项目
        return queryset.filter(id__in=passed_ids), tokens

    def apply_filter(self, queryset):
        tokens = 0
        # 优先尝试使用关键字过滤
        if self.filter_method in [self.KEYWORD_ONLY, self.BOTH]:
            queryset = self.apply_keywords_filter(queryset)

        # 检查是否需要AI过滤
        if self.filter_method in [self.AI_ONLY, self.BOTH] and self.agent:
            queryset, tokens = self.apply_ai_filter(queryset)

        if tokens > 0:
            self.total_tokens += tokens
            self.save()

        return queryset

    def needs_re_evaluation(self, result, entry):
        """检查缓存是否失效"""
        # 1. 如果从未评估过
        if result.passed is None:
            return True

        # 2. 检查条目内容是否更新
        if entry.updated and entry.updated > result.last_updated:
            return True

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
                "agent_content_type_id",
                "agent_object_id",
                "filter_prompt",
                "filter_method",
                "filter_original_title",
                "filter_original_content",
                "filter_translated_title",
                "filter_translated_content",
            ]
            # 检查关键词是否变化
            keyword_changed = False
            original_keywords = sorted([tag.name for tag in original.keywords.all()])
            current_keywords = sorted([tag.name for tag in self.keywords.all()])
            if original_keywords != current_keywords:
                keyword_changed = True

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
        logging.debug(f"Cleared cache for filter {self.name}")


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

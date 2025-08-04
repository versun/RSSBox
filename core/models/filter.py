from django.db import models
from django.utils.translation import gettext_lazy as _
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from tagulous.models import TagField
from utils import text_handler
import json


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
    agent_object_id = models.PositiveIntegerField(
        null=True, blank=True, default=None
    )
    agent = GenericForeignKey("agent_content_type", "agent_object_id")
    filter_prompt = models.TextField(
        _("Filter Prompt"),
        blank=True,
        null=True
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
    
    def apply_ai_filter(self, queryset):
        """
        应用AI过滤器到查询集，使用AI代理处理内容
        :param queryset: 要过滤的查询集
        :return: 过滤后的查询集
        """
        if not self.agent:
            return queryset

        json_data = {}
        
        for entry in queryset:
            # 准备要发送给AI的内容
            json_data = {}
            if self.filter_original_title:
                json_data["original_title"] = entry.original_title
            if self.filter_original_content:
                json_data["original_content"] = text_handler.clean_content(entry.original_content)
            if self.filter_translated_title:
                json_data["translated_title"] = entry.translated_title
            if self.filter_translated_content:
                json_data["translated_content"] = text_handler.clean_content(entry.translated_content)

            text_str = json.dumps(json_data, ensure_ascii=False)
# TODO: 不能每次点击rss就调用AI过滤器，这样会导致性能问题，应该要缓存，等更新时再调用
            results = self.agent.filter(text=text_str, system_prompt=self.filter_prompt)
            if results: # Passed the filter
                continue
            else: # Blocked by the filter
                # 从查询集中移除被过滤的条目
                queryset = queryset.exclude(id=entry.id)

        return queryset
    
    def apply_filter(self, queryset):
        if self.filter_method == self.KEYWORD_ONLY:
            return self.apply_keywords_filter(queryset)
        elif self.filter_method == self.AI_ONLY:
            return self.apply_ai_filter(queryset)
        elif self.filter_method == self.BOTH:
            queryset = self.apply_keywords_filter(queryset)
            return self.apply_ai_filter(queryset)
        else:
            raise ValueError("Invalid filter method specified.")

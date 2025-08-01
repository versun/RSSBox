from django.db import models
from django.utils.translation import gettext_lazy as _
from tagulous.models import TagField


class Filter(models.Model):
    INCLUDE = True
    EXCLUDE = False
    OPERATION_CHOICES = (
        (INCLUDE, _("Include - Only show items containing these keywords")),
        (EXCLUDE, _("Exclude - Hide items containing these keywords")),
    )

    FIELD_CHOICES = (
        ("original_title", _("Original Title")),
        ("original_content", _("Original Content")),
        ("translated_title", _("Translated Title")),
        ("translated_content", _("Translated Content")),
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

    def __str__(self):
        return f"{self.name}"

    class Meta:
        verbose_name = _("Filter")
        verbose_name_plural = _("Filter")

    def apply_filter(self, queryset):
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

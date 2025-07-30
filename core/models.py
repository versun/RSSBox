import os
import uuid
from config import settings

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.core.validators import MinValueValidator, MaxValueValidator
from tagulous.models import SingleTagField, TagField

class Feed(models.Model):
    name = models.CharField(
        max_length=255, blank=True, null=True, verbose_name=_("Name")
    )
    subtitle = models.CharField(
        max_length=255, blank=True, null=True, verbose_name=_("Subtitle")
    )
    slug = models.SlugField(
        _("URL Slug"),
        max_length=255,
        unique=True,
        blank=True,
        null=True,
    )
    link = models.URLField(
        _("Link"),
        blank=True,
        null=True,
    )
    author = models.CharField(
        _("Author"),
        max_length=255,
        blank=True,
        null=True,
    )
    language = models.CharField(
        _("Language"),
        max_length=255,
        blank=True,
        null=True,
    )
    pubdate = models.DateTimeField(
        _("Pubdate"),
        blank=True,
        null=True,
    )
    updated = models.DateTimeField(
        _("Updated"),
        blank=True,
        null=True,
    )
    feed_url = models.URLField(_("Feed URL"))
    fetch_status = models.BooleanField(
        _("Fetch Status"),
        null=True,
        editable=False,
    )

    update_frequency = models.IntegerField(
        _("Update Frequency"),
        default=30,
        help_text=_("Minutes"),
    )
    max_posts = models.IntegerField(
        _("Max Posts"),
        default=os.getenv("default_max_posts", 20),
        help_text=_("Max number of posts to be fetched"),
    )
    
    fetch_article = models.BooleanField(
        _("Fetch Original Article"),
        default=False,
        help_text=_("Fetch original article from the website."),
    )

    TRANSLATION_DISPLAY_CHOICES = [
        (0, _("Only Translation")),
        (1, _("Translation | Original")),
        (2, _("Original | Translation")),
    ]
    translation_display = models.IntegerField(
        _("Translation Display"), default=0, choices=TRANSLATION_DISPLAY_CHOICES
    )  # 0: Only Translation, 1: Translation || Original, 2: Original || Translation

    target_language = models.CharField(
        _("Language"),
        choices=settings.TRANSLATION_LANGUAGES,
        max_length=50,
        default=settings.DEFAULT_TARGET_LANGUAGE,
    )
    translate_title = models.BooleanField(_("Translate Title"), default=False)
    translate_content = models.BooleanField(_("Translate Content"), default=False)
    summary = models.BooleanField(_("Summary"), default=False)

    translation_status = models.BooleanField(
        _("Translation Status"),
        null=True,
        editable=False,
    )

    translator_content_type = models.ForeignKey(
        ContentType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        default=None,
        related_name="translator",
    )
    translator_object_id = models.PositiveIntegerField(
        null=True, blank=True, default=None
    )
    translator = GenericForeignKey("translator_content_type", "translator_object_id")

    summarizer_content_type = models.ForeignKey(
        ContentType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        default=None,
        related_name="summarizer",
    )
    summarizer_object_id = models.PositiveIntegerField(
        null=True, blank=True, default=None
    )
    summarizer = GenericForeignKey("summarizer_content_type", "summarizer_object_id")

    summary_detail = models.FloatField(
        _("Summary Detail"),
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        default=0.0,
        blank=True,
        null=True,
        help_text=_(
            "Level of detail of summaries of longer articles. 0: Normal, 1: Most detailed (cost more tokens)"
        ),
    )
    filters = models.ManyToManyField(
        "Filter",
        blank=True,
        related_name="feeds",
        verbose_name=_("Filters"),
        help_text=_("Filters to apply to the feed entries"),
    )

    additional_prompt = models.TextField(
        _("Addtional Prompt"),
        default=None,
        blank=True,
        null=True,
        help_text=_("Addtional Prompt for translation and summary"),
    )
    category = SingleTagField(
        force_lowercase=True, blank=True, help_text=_("Enter a category string")
    )

    total_tokens = models.IntegerField(_("Tokens Cost"), default=0)
    total_characters = models.IntegerField(_("Characters Cost"), default=0)

    last_translate = models.DateTimeField(
        _("Last translate"),
        blank=True,
        null=True,
        editable=False,
        help_text=_("Last time the feed was translated"),
    )

    last_fetch = models.DateTimeField(
        _("Last Fetch(UTC)"),
        default=None,
        blank=True,
        null=True,
        editable=False,
        help_text=_("Last time the feed was fetched"),
    )
    etag = models.CharField(
        max_length=255,
        default="",
        editable=False,
        null=True,
        blank=True,
    )

    log = models.TextField(
        _("Log"),
        default="",
        blank=True,
        null=True,
        help_text=_("Log for the feed, useful for debugging"),
    )

    def __str__(self):
        return self.feed_url

    class Meta:
        verbose_name = _("Feed")
        verbose_name_plural = _("Feeds")
        constraints = [
            models.UniqueConstraint(
                fields=["feed_url", "target_language"], name="unique_feed_lang"
            )
        ]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"{self.feed_url}:{self.target_language}:{settings.SECRET_KEY}",
            ).hex

        thresholds = [5, 15, 30, 60, 1440, 10080]
        for threshold in thresholds:
            if self.update_frequency <= threshold:
                self.update_frequency = threshold
                break

        if len(self.log.encode("utf-8")) > 2048:
            self.log = self.log[-2048:]

        if not self.translator_content_type_id:
            self.translator_content_type_id = None
            self.translator_object_id = None
        if not self.summarizer_content_type_id:
            self.summarizer_content_type_id = None
            self.summarizer_object_id = None

        super(Feed, self).save(*args, **kwargs)

    def get_translation_display(self):
        return dict(self.TRANSLATION_DISPLAY_CHOICES)[self.translation_display]

    @property
    def filtered_entries(self):
        queryset = self.entries.all()
        for filter_obj in self.filters.all():
            queryset = filter_obj.apply_filter(queryset)
        return queryset

class Entry(models.Model):
    feed = models.ForeignKey(
        Feed, on_delete=models.CASCADE, related_name="entries"
    )
    link = models.URLField(null=False)
    author = models.CharField(max_length=255, null=True, blank=True)
    pubdate = models.DateTimeField(null=True, blank=True)
    updated = models.DateTimeField(null=True, blank=True)
    guid = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    enclosures_xml = models.TextField(null=True, blank=True)

    original_title = models.CharField(max_length=255, null=True, blank=True)
    translated_title = models.CharField(max_length=255, null=True, blank=True)
    original_content = models.TextField(null=True, blank=True)
    translated_content = models.TextField(null=True, blank=True)
    original_summary = models.TextField(null=True, blank=True)
    ai_summary = models.TextField(null=True, blank=True)

    def __str__(self):
        return self.original_title

    class Meta:
        verbose_name = _("Entry")
        verbose_name_plural = _("Entries")

class Filter(models.Model):
    INCLUDE = True
    EXCLUDE = False
    OPERATION_CHOICES = (
        (INCLUDE, _("Include - Match only these keywords")),
        (EXCLUDE, _("Exclude - Exclude that matches these keywords")),
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
        keywords = self.keywords.values_list('name', flat=True)
        
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
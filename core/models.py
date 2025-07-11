import os
import uuid
# import re
from config import settings

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.core.validators import MinValueValidator, MaxValueValidator
from tagulous.models import SingleTagField


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
    quality = models.BooleanField(
        _("Best Quality"),
        default=False,
        help_text=_(
            "Formatting such as hyperlinks, bold, italics, etc. will be lost for optimal translation quality."
        ),
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


class AISummaryReport(models.Model):
    name = models.CharField(
        _("Report Name"),
        max_length=255,
        blank=True,
        null=True,
    )
    slug = models.SlugField(
        _("URL Slug"),
        max_length=255,
        unique=True,
        blank=True,
        null=True,
    )
    target_language = models.CharField(
        _("Language"),
        choices=settings.TRANSLATION_LANGUAGES,
        max_length=50,
        default=settings.DEFAULT_TARGET_LANGUAGE,
    )
    reporter_content_type = models.ForeignKey(
        ContentType,
        null=True,
        on_delete=models.SET_NULL,
        related_name="reporter",
    )
    reporter_object_id = models.PositiveIntegerField(
        null=True, blank=True, default=None
    )
    reporter = GenericForeignKey("reporter_content_type", "reporter_object_id")
    report_prompt = models.TextField(
        _("Report Prompt"),
        default=settings.default_report_prompt,
        help_text=_("Custom prompt for generating AI summaries report"),
    )
    publish_days = models.CharField(
        _("Publish Days"),
        max_length=7,
        default="1234567",  # Default to every day
        help_text=_(
            "A string of 7 characters representing days of the week (Mon-Sun), where 1 means Monday, 2 means Tuesday, etc."
        ),
    )
    related_feeds = models.ManyToManyField(
        Feed,
        blank=True,
        related_name="ai_summary_reports",
        verbose_name=_("Related Feeds"),
    )
    # 获取feed的时间范围：按天数填写，1就是今天，2就是2天
    days_range = models.IntegerField(
        _("Days Range"),
        default=1,
        help_text=_("Number of days to fetch entries from related feeds for the report"),
        validators=[MinValueValidator(1)],
    )

    created_at = models.DateTimeField(_("Created At"), auto_now_add=True)
    updated_at = models.DateTimeField(_("Updated At"), auto_now=True)
    total_tokens = models.IntegerField(
        _("Total Tokens"),
        default=0,
        help_text=_("Total tokens used for AI summaries in this report"),
    )
    log = models.TextField(
        _("Log"),
        default="",
        blank=True,
        null=True,
    )

    def __str__(self):
        return f"{self.name} - {self.slug}"

    class Meta:
        verbose_name = _("AI Summary Report")
        verbose_name_plural = _("AI Summary Reports")
    
    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = uuid.uuid4().hex

        if len(self.log.encode("utf-8")) > 2048:
            self.log = self.log[-2048:]
        
        super(AISummaryReport, self).save(*args, **kwargs)



class Entry(models.Model):
    feed = models.ForeignKey(
        Feed, on_delete=models.CASCADE, related_name="entries", null=True, blank=True
    )
    ai_summary_report = models.ForeignKey(
        AISummaryReport,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="reports",
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
        # constraints = [
        #     models.UniqueConstraint(
        #         fields=["feed", "guid"], name="unique_entry_guid"
        #     )
        # ]

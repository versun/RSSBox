from django.db import models
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
from config import settings
import uuid

from core.models.feed import Feed
from django.db.models.signals import post_delete
from django.dispatch import receiver


class Digest(models.Model):
    """
    Digest model for generating AI-powered daily/weekly briefings from RSS feeds.

    This model stores configuration for automatic digest generation, including
    which tags to monitor, how many articles to process, and what AI agent to use
    for content generation.
    """

    name = models.CharField(
        max_length=200,
        verbose_name=_("Name"),
        help_text=_("Name of the digest (e.g., 'Tech Daily', 'Weekly Summary')"),
    )

    slug = models.SlugField(
        unique=True,
        blank=True,
        null=True,
        verbose_name=_("Slug"),
        help_text=_("URL-friendly version of the name, auto-generated"),
    )

    description = models.TextField(
        blank=True,
        verbose_name=_("Description"),
        help_text=_("Optional description of what this digest covers"),
    )

    status = models.BooleanField(
        _("Generation Status"),
        null=True,
        editable=False,
        help_text=_("Whether the last generation was successful"),
    )

    tags = models.ManyToManyField(
        "Tag",
        related_name="digests",
        verbose_name=_("Tags"),
        help_text=_(
            "Tags to include in this digest. Articles from feeds with these tags will be processed"
        ),
    )

    summarizer = models.ForeignKey(
        "OpenAIAgent",
        on_delete=models.CASCADE,
        limit_choices_to={"valid": True},
        verbose_name=_("AI Summarizer"),
        help_text=_(
            "OpenAI agent to use for generating digest content (only valid agents are shown)"
        ),
    )

    days_range = models.IntegerField(
        default=1,
        verbose_name=_("Days Range"),
        help_text=_(
            "Number of days to look back for articles (1 = today only, 7 = past week)"
        ),
    )

    target_language = models.CharField(
        max_length=50,
        choices=settings.TRANSLATION_LANGUAGES,
        default=settings.DEFAULT_TARGET_LANGUAGE,
        verbose_name=_("Target Language"),
        help_text=_("Language of the generated digest output"),
    )

    prompt = models.TextField(
        default=settings.default_digest_prompt,
        verbose_name=_("Prompt"),
        help_text=_(
            "AI prompt for generating digest content. Use {digest_name}, {date}, {target_language} as placeholders"
        ),
    )

    is_active = models.BooleanField(
        default=True,
        verbose_name=_("Active"),
        help_text=_("Whether this digest should be automatically generated"),
    )

    last_generated = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Last Generated"),
        help_text=_("When this digest was last generated"),
    )

    publish_days = models.JSONField(
        default=list,
        verbose_name=_("Publish Days"),
        help_text=_("Days of week to publish (e.g., ['monday', 'tuesday', 'friday'])"),
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_("Created At"),
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name=_("Updated At"),
    )

    log = models.TextField(
        _("Log"),
        default="",
        blank=True,
        null=True,
        help_text=_("Log for the digest, useful for debugging"),
    )

    total_tokens = models.IntegerField(_("Tokens Cost"), default=0)

    class Meta:
        verbose_name = _("Digest")
        verbose_name_plural = _("Digests")
        ordering = ["-updated_at"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"{self.name}:{self.target_language}:{settings.SECRET_KEY}",
            ).hex
        super(Digest, self).save(*args, **kwargs)

    def should_generate_today(self):
        """
        Check if digest should be generated today based on last generation time and publish days.

        Returns:
            bool: True if digest should be generated, False otherwise
        """
        if not self.is_active:
            return False

        # Check if today is a publish day
        if not self.is_publish_day():
            return False

        if not self.last_generated:
            return True

        today = timezone.now().date()
        last_gen_date = self.last_generated.date()

        return today > last_gen_date

    def is_publish_day(self, date=None):
        """
        Check if the given date (or today) is a publish day for this digest.

        Args:
            date: datetime.date object, defaults to today

        Returns:
            bool: True if this is a publish day, False otherwise
        """
        if date is None:
            date = timezone.now().date()

        # Get weekday name (Monday, Tuesday, etc.)
        weekday_name = date.strftime("%A").lower()

        # Check if this day is in the publish_days list
        return weekday_name in (self.publish_days or [])

    def get_publish_days_list(self):
        """
        Get list of enabled publish days as weekday names.

        Returns:
            list: List of weekday names (e.g., ['Monday', 'Tuesday'])
        """
        if not self.publish_days:
            return []

        # Convert to proper case weekday names
        weekday_map = {
            "monday": "Monday",
            "tuesday": "Tuesday",
            "wednesday": "Wednesday",
            "thursday": "Thursday",
            "friday": "Friday",
            "saturday": "Saturday",
            "sunday": "Sunday",
        }

        enabled_days = []
        for day in self.publish_days:
            if day and day.lower() in weekday_map:
                enabled_days.append(weekday_map[day.lower()])

        return enabled_days

    def get_articles_for_digest(self):
        """
        Get articles that should be included in this digest.

        Returns:
            QuerySet: Entry objects filtered by tags and date range
        """
        from core.models.entry import Entry
        from datetime import timedelta

        end_date = timezone.now()
        start_date = end_date - timedelta(days=self.days_range)

        # 正确获取digest tags对应的feeds的entries
        digest_tags = self.tags.all()
        if not digest_tags.exists():
            return Entry.objects.none()

        # 获取所有相关feeds
        related_feeds = [tag.feeds.all() for tag in digest_tags]
        feed_ids = set()
        for feeds in related_feeds:
            feed_ids.update(feeds.values_list("id", flat=True))

        if not feed_ids:
            return Entry.objects.none()

        # 从这些feeds获取entries
        entries = (
            Entry.objects.filter(
                feed_id__in=feed_ids, pubdate__gte=start_date, pubdate__lte=end_date
            )
            .distinct()
            .order_by("-pubdate")
        )

        return entries

    def get_digest_feed(self) -> Feed:
        """获取或创建 Digest 专用 Feed"""
        feed_url = f"{settings.SITE_URL.rstrip('/')}/rss/digest/{self.slug}"

        defaults = {
            "name": f"Digest:{self.slug} | @Digest@hide",
            "subtitle": f"AI Digest for {self.name}",
            "link": f"{settings.SITE_URL.rstrip('/')}/rss/digest/{self.slug}",
            "author": self.name or "Digest",
            "language": self.target_language,
            "update_frequency": 1440,
            "fetch_article": False,
            "translate_title": False,
            "translate_content": False,
            "summary": False,
            "target_language": self.target_language,
        }

        return Feed.objects.get_or_create(
            feed_url=feed_url,
            target_language=self.target_language,
            defaults=defaults,
        )[0]


@receiver(post_delete, sender=Digest)
def delete_associated_feed_and_entries(sender, instance: "Digest", **kwargs):
    """
    当删除一个 Digest 时，删除其专用 Feed，从而通过 Entry.feed 的 CASCADE 删除相关 Entries。
    注意：不要使用 get_or_create 以避免误创建。
    """
    try:
        feed_url = f"{settings.SITE_URL.rstrip('/')}/rss/digest/{instance.slug}"
        Feed.objects.filter(
            feed_url=feed_url,
            target_language=instance.target_language,
        ).delete()
    except Exception:
        # 静默失败以避免阻断 Digest 删除流程；实际错误会在上层日志中体现
        pass

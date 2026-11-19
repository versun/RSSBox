from django.db import models
from django.utils.text import slugify as django_slugify
from django.utils.translation import gettext_lazy as _
from autoslug import AutoSlugField


def unicode_slugify(value):
    """保留 Unicode 字符（如中文）的 slugify，供 AutoSlugField 使用。"""
    return django_slugify(value, allow_unicode=True)


class Tag(models.Model):
    name = models.CharField(
        max_length=255, verbose_name=_("Name"), default="Unnamed Tag"
    )

    filters = models.ManyToManyField(
        "Filter",
        blank=True,
        related_name="tags",
        verbose_name=_("Filters"),
    )

    apply_feed_filters = models.BooleanField(
        _("Apply feed filters"),
        default=False,
        help_text=_(
            "Use each feed's own filtered output (as in the feed's own RSS link) "
            "instead of all its entries. The tag's filters are applied afterwards."
        ),
    )

    slug = AutoSlugField(
        verbose_name=_("URL Slug"),
        populate_from="name",
        unique=True,
        slugify=unicode_slugify,
    )

    total_tokens = models.PositiveIntegerField(_("Tokens Cost"), default=0)

    last_updated = models.DateTimeField(
        _("Last updated"),
        blank=True,
        null=True,
        editable=False,
    )

    etag = models.CharField(
        max_length=255,
        default="",
        editable=False,
        null=True,
        blank=True,
    )

    def __str__(self):
        return self.slug

    def save(self, *args, **kwargs):
        apply_feed_filters_changed = False
        update_fields = kwargs.get("update_fields")
        check_name = update_fields is None or "name" in update_fields
        check_apply_feed_filters = (
            update_fields is None or "apply_feed_filters" in update_fields
        )
        if self.pk and (check_name or check_apply_feed_filters):
            old_instance = (
                Tag.objects.filter(pk=self.pk)
                .only("name", "apply_feed_filters")
                .first()
            )
            if old_instance is not None:
                if check_name and old_instance.name != self.name:
                    self.slug = None  # 让AutoSlugField重新生成
                    if update_fields is not None and "slug" not in update_fields:
                        kwargs["update_fields"] = [*update_fields, "slug"]
                if check_apply_feed_filters:
                    apply_feed_filters_changed = (
                        old_instance.apply_feed_filters != self.apply_feed_filters
                    )
        super().save(*args, **kwargs)
        if apply_feed_filters_changed:
            # Different entries now make up the output: drop the cached copy.
            from core.models.filter import bump_cache_version

            bump_cache_version("tag", self.pk)

from django.db import models
from django.utils.translation import gettext_lazy as _
from autoslug import AutoSlugField

class Tag(models.Model):
    name = models.CharField(
        max_length=255, blank=True, null=True, verbose_name=_("Name")
    )

    filters = models.ManyToManyField(
        "Filter",
        blank=True,
        related_name="tags",
        verbose_name=_("Filters"),
    )

    slug = AutoSlugField(
        verbose_name=_("URL Slug"),
        populate_from='name',
        unique=True,
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
        if self.pk:  # 如果是更新操作
            old_instance = Tag.objects.get(pk=self.pk)
            if old_instance.name != self.name:  # 如果name被修改
                self.slug = None  # 设置slug为None，让AutoSlugField重新生成
        super().save(*args, **kwargs)

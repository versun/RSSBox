from django.db import models
from django.utils.translation import gettext_lazy as _
from core.models.feed import Feed


class Entry(models.Model):
    feed = models.ForeignKey(Feed, on_delete=models.CASCADE, related_name="entries")
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

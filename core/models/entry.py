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

    # SHA-256 of the RSS payload the publisher served for this item.
    # ``original_content`` cannot serve as the change signal: the
    # translation pipeline replaces it with a fetched full article, so
    # comparing it against the next RSS payload always reports a change
    # and wipes paid translations on every run (issue #181). NULL on
    # rows written before the field existed.
    source_hash = models.CharField(max_length=64, null=True, blank=True)

    def __str__(self):
        return self.original_title

    class Meta:
        verbose_name = _("Entry")
        verbose_name_plural = _("Entries")
        indexes = [
            # Every display/AI pipeline window is the latest max_posts by
            # pubdate per feed; without this index the sort scans the whole
            # per-feed history.
            models.Index(
                fields=["feed", "-pubdate"], name="entry_feed_pubdate_idx"
            ),
            # Fetch dedupe is a per-(feed, guid) lookup on every run.
            models.Index(
                fields=["feed", "guid"], name="entry_feed_guid_idx"
            ),
        ]

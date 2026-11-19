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
    # Explicit data-lineage flag indicating whether ``fetch_article`` has
    # already succeeded for the current RSS payload. Kept decoupled from
    # EntryTaskState retry counters so a failed web fetch can still be retried
    # while a successful fetch is never overwritten during translation retries.
    article_fetched = models.BooleanField(default=False)

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


class EntryTaskState(models.Model):
    """Per-(entry, stage) retry budget, backoff, and pause state (issue #186).

    Created lazily when an entry fails or is manually paused so healthy entries
    incur zero extra writes. Prevents slow/failing articles from replaying
    indefinitely across scheduled runs and billing repeatedly.
    """

    STAGE_TITLE = "title"
    STAGE_CONTENT = "content"
    STAGE_SUMMARY = "summary"
    STAGE_CHOICES = [
        (STAGE_TITLE, _("Title")),
        (STAGE_CONTENT, _("Content")),
        (STAGE_SUMMARY, _("Summary")),
    ]

    STATUS_PENDING = "pending"
    STATUS_FAILED = "failed"
    STATUS_PAUSED = "paused"
    STATUS_SUCCEEDED = "succeeded"
    STATUS_CHOICES = [
        (STATUS_PENDING, _("Pending")),
        (STATUS_FAILED, _("Failed (Waiting Retry)")),
        (STATUS_PAUSED, _("Paused")),
        (STATUS_SUCCEEDED, _("Succeeded")),
    ]

    entry = models.ForeignKey(
        Entry, on_delete=models.CASCADE, related_name="task_states"
    )
    stage = models.CharField(_("Stage"), max_length=16, choices=STAGE_CHOICES)
    status = models.CharField(
        _("Status"),
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )
    attempt_count = models.PositiveIntegerField(_("Attempts"), default=0)
    last_error_type = models.CharField(
        _("Last Error Type"), max_length=32, blank=True, default=""
    )
    last_error = models.TextField(_("Last Error"), blank=True, default="")
    next_retry_at = models.DateTimeField(
        _("Next Retry At"), null=True, blank=True, db_index=True
    )
    last_attempt_at = models.DateTimeField(
        _("Last Attempt At"), null=True, blank=True
    )
    paused_manually = models.BooleanField(_("Paused Manually"), default=False)
    estimated_unbilled_tokens = models.PositiveIntegerField(
        _("Estimated Unbilled Tokens (Timeout)"),
        default=0,
        help_text=_(
            "Estimated input tokens sent on timed-out requests where upstream "
            "usage was unavailable and may still have been billed."
        ),
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Entry #{self.entry_id} [{self.stage}] ({self.status})"

    class Meta:
        verbose_name = _("Entry Task State")
        verbose_name_plural = _("Entry Task States")
        constraints = [
            models.UniqueConstraint(
                fields=["entry", "stage"], name="unique_entry_stage_state"
            )
        ]
        indexes = [
            models.Index(
                fields=["status", "next_retry_at"],
                name="entry_task_status_retry_idx",
            ),
        ]


class TranslationChunkCache(models.Model):
    """Persistent DB-backed cache for completed chunks of a multi-chunk AI call.

    Survives across scheduled cron subprocesses and container restarts so a
    later retry only re-translates failed chunks. Deleted once the parent entry
    commits its completed translation, or invalidated automatically when source
    text, model, language, prompt, or parameters change (part of cache_key).
    """

    entry = models.ForeignKey(
        Entry,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="chunk_caches",
    )
    stage = models.CharField(max_length=16, blank=True, default="content")
    cache_key = models.CharField(max_length=64, unique=True, db_index=True)
    translated_text = models.TextField()
    # Unbilled token balance for this cached chunk. Zeroed atomically when
    # billed to Feed.total_tokens so normal retries never double-bill, while
    # crash/OOM recovery (when the prior process died before billing) still
    # accounts for the tokens on cache hit.
    tokens = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    def __str__(self):
        return f"Chunk {self.cache_key[:12]} (entry={self.entry_id}, stage={self.stage})"

    class Meta:
        verbose_name = _("Translation Chunk Cache")
        verbose_name_plural = _("Translation Chunk Caches")
        indexes = [
            models.Index(
                fields=["entry", "stage"], name="chunk_cache_entry_stage_idx"
            ),
        ]

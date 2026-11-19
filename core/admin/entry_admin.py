import hashlib
import logging
from django.contrib import admin, messages
from django.db import close_old_connections, transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from core.admin import core_admin_site
from core.management.commands.feed_updater import _try_acquire_feed_lock
from core.models import Entry, EntryTaskState, Feed
from core.models.filter import bump_cache_version
from core.tasks.summarize_feeds import summarize_feed
from core.tasks.task_manager import task_manager
from core.tasks.translate_feeds import translate_feed
from core.tasks.utils import reset_entry_task_states_queryset

logger = logging.getLogger(__name__)

# Causal execution order within a single per-feed lock:
# content may fetch full article text and invalidate ai_summary, so summary
# must always run after content.
_STAGE_ORDER = (
    EntryTaskState.STAGE_TITLE,
    EntryTaskState.STAGE_CONTENT,
    EntryTaskState.STAGE_SUMMARY,
)


def _feed_has_unpaused_pending_work(feed: Feed) -> bool:
    """True when the feed window still has unfinished, non-paused entries."""
    window_entries = list(
        feed.entries.order_by("-pubdate", "-id")[: feed.max_posts].only(
            "pk",
            "original_title",
            "translated_title",
            "original_content",
            "translated_content",
            "ai_summary",
        )
    )
    if not window_entries:
        return False

    window_ids = [e.pk for e in window_entries]
    paused_pairs = set(
        EntryTaskState.objects.filter(
            entry_id__in=window_ids,
            status=EntryTaskState.STATUS_PAUSED,
        ).values_list("entry_id", "stage")
    )

    for entry in window_entries:
        if (
            feed.translate_title
            and not entry.translated_title
            and (entry.original_title or "").strip()
            and (entry.pk, EntryTaskState.STAGE_TITLE) not in paused_pairs
        ):
            return True
        if (
            feed.translate_content
            and not entry.translated_content
            and (entry.original_content or feed.fetch_article)
            and (entry.pk, EntryTaskState.STAGE_CONTENT) not in paused_pairs
        ):
            return True
        if (
            feed.summary
            and entry.ai_summary is None
            and (entry.pk, EntryTaskState.STAGE_SUMMARY) not in paused_pairs
        ):
            return True
    return False


def retry_feed_entry_stages(
    feed_id: int, stage_entries: dict[str, list[int]]
) -> bool:
    """Worker entrypoint for retrying one or more stages of a feed sequentially
    under a single per-feed lock in causal order (title -> content -> summary)."""
    close_old_connections()
    feed_lock = None
    try:
        feed = Feed.objects.filter(pk=feed_id).first()
        if feed is None:
            logger.warning("Skip single-entry retry: feed %s not found", feed_id)
            return False

        feed_lock = _try_acquire_feed_lock(feed)
        if feed_lock is None:
            logger.info(
                "Skipping single-entry retry for feed %s: another process "
                "is already updating this feed",
                feed_id,
            )
            return False

        original_log = feed.log
        original_status = feed.translation_status
        saved = False
        all_ok = True
        ran_any = False

        for stage in _STAGE_ORDER:
            entry_ids = stage_entries.get(stage)
            if not entry_ids:
                continue
            sorted_ids = sorted(set(entry_ids))
            if stage in (EntryTaskState.STAGE_TITLE, EntryTaskState.STAGE_CONTENT):
                ran_any = True
                stage_saved = bool(
                    translate_feed(feed, target_field=stage, entry_ids=sorted_ids)
                )
                saved = saved or stage_saved
                if feed.translation_status is False:
                    all_ok = False
            elif stage == EntryTaskState.STAGE_SUMMARY and feed.summarizer:
                pending_ids = list(
                    Entry.objects.filter(
                        pk__in=sorted_ids, feed=feed, ai_summary__isnull=True
                    ).values_list("pk", flat=True)
                )
                if pending_ids:
                    ran_any = True
                    stage_ok = summarize_feed(
                        feed,
                        min_chunk_size=feed.summarizer.min_size(),
                        max_chunk_size=feed.summarizer.max_size(),
                        max_context_tokens=feed.summarizer.max_tokens,
                        entry_ids=pending_ids,
                    )
                    if not stage_ok:
                        all_ok = False
                    stage_saved = Entry.objects.filter(
                        pk__in=pending_ids, ai_summary__isnull=False
                    ).exists()
                    saved = saved or stage_saved

        update_fields = []
        if saved:
            feed.last_translate = timezone.now()
            update_fields.append("last_translate")

        if ran_any:
            if not all_ok:
                new_status = False
            elif (
                feed.translate_title or feed.translate_content or feed.summary
            ) and not _feed_has_unpaused_pending_work(feed):
                new_status = True
            else:
                new_status = original_status
            if new_status != original_status:
                feed.translation_status = new_status
                update_fields.append("translation_status")
            else:
                # Restore original in-memory value if translate_feed mutated it
                # without us intending to persist a change.
                feed.translation_status = original_status

        if feed.log != original_log:
            update_fields.append("log")
        if update_fields:
            feed.save(update_fields=update_fields)

        if saved:
            bump_cache_version("rss", feed.pk)
            for tag_pk in feed.tags.values_list("pk", flat=True):
                bump_cache_version("tag", tag_pk)
        return saved
    finally:
        if feed_lock is not None:
            feed_lock.close()
        close_old_connections()


def retry_single_entry_stage(feed_id: int, entry_ids: list[int], stage: str) -> bool:
    """Single-stage convenience wrapper around ``retry_feed_entry_stages``."""
    return retry_feed_entry_stages(feed_id, {stage: entry_ids})


@admin.action(description=_("Pause selected entry tasks"))
def pause_entry_tasks(modeladmin, request, queryset):
    updated = queryset.update(
        status=EntryTaskState.STATUS_PAUSED,
        paused_manually=True,
        next_retry_at=None,
        updated_at=timezone.now(),
    )
    modeladmin.message_user(
        request,
        _("Paused %(count)d entry task(s).") % {"count": updated},
        messages.SUCCESS,
    )


@admin.action(description=_("Resume selected entry tasks (reset retry budget)"))
def resume_entry_tasks(modeladmin, request, queryset):
    updated = reset_entry_task_states_queryset(queryset, include_manual_pause=True)
    modeladmin.message_user(
        request,
        _("Resumed %(count)d entry task(s). They will run on the next cycle.")
        % {"count": updated},
        messages.SUCCESS,
    )


@admin.action(description=_("Retry selected entry tasks now"))
def retry_entry_tasks_now(modeladmin, request, queryset):
    items = list(queryset.select_related("entry__feed"))
    if not items:
        return

    ids = [item.pk for item in items]
    reset_entry_task_states_queryset(
        EntryTaskState.objects.filter(pk__in=ids), include_manual_pause=True
    )

    # Group by feed_id (matching the per-feed flock granularity) so retrying
    # multiple stages of the same feed runs sequentially under one lock instead
    # of racing on the non-blocking flock and silently dropping sibling stages.
    grouped_by_feed: dict[int, dict[str, list[int]]] = {}
    for item in items:
        stage_map = grouped_by_feed.setdefault(item.entry.feed_id, {})
        stage_map.setdefault(item.stage, []).append(item.entry_id)

    submissions: list[tuple[str, int, dict[str, list[int]]]] = []
    for feed_id, stage_map in grouped_by_feed.items():
        normalized = {
            stage: sorted(set(stage_map[stage]))
            for stage in _STAGE_ORDER
            if stage in stage_map
        }
        sig_parts = [
            f"{stage}:{','.join(map(str, eids))}"
            for stage, eids in normalized.items()
        ]
        sig = "|".join(sig_parts)
        digest = hashlib.sha256(sig.encode()).hexdigest()[:12]
        task_name = f"retry_feed_entries_{feed_id}_{digest}"
        submissions.append((task_name, feed_id, normalized))

    def _submit_all():
        for task_name, feed_id, normalized in submissions:
            task_manager.submit_task(
                task_name, retry_feed_entry_stages, feed_id, normalized
            )

    transaction.on_commit(_submit_all)

    modeladmin.message_user(
        request,
        _("Submitted immediate retry for %(count)d entry task(s).")
        % {"count": len(items)},
        messages.SUCCESS,
    )


class EntryTaskStateAdmin(admin.ModelAdmin):
    list_display = [
        "entry_link",
        "feed_link",
        "stage",
        "status",
        "attempt_count",
        "last_error_type",
        "next_retry_at",
        "estimated_unbilled_tokens",
        "paused_manually",
        "updated_at",
    ]
    list_filter = [
        "status",
        "stage",
        "last_error_type",
        "paused_manually",
        "entry__feed",
    ]
    search_fields = [
        "entry__original_title",
        "entry__link",
        "entry__feed__name",
        "last_error",
    ]
    readonly_fields = [
        "entry",
        "stage",
        "attempt_count",
        "last_error_type",
        "last_error",
        "last_attempt_at",
        "estimated_unbilled_tokens",
        "updated_at",
    ]
    actions = [pause_entry_tasks, resume_entry_tasks, retry_entry_tasks_now]
    list_select_related = ["entry", "entry__feed"]
    list_per_page = 30

    def has_add_permission(self, request):
        return False

    @admin.display(description=_("Entry"), ordering="entry__original_title")
    def entry_link(self, obj):
        title = obj.entry.original_title or f"Entry #{obj.entry_id}"
        link = (obj.entry.link or "").strip()
        if link.startswith(("http://", "https://")):
            return format_html(
                "<a href='{}' target='_blank' rel='noopener noreferrer'>#{} {}</a>",
                link,
                obj.entry_id,
                title[:80],
            )
        return f"#{obj.entry_id} {title[:80]}"

    @admin.display(description=_("Feed"), ordering="entry__feed__name")
    def feed_link(self, obj):
        feed = obj.entry.feed
        url = reverse("admin:core_feed_change", args=[feed.pk])
        return format_html("<a href='{}'>{}</a>", url, feed.name or feed.feed_url)


core_admin_site.register(EntryTaskState, EntryTaskStateAdmin)

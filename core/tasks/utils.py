from datetime import timedelta
import logging
import time

import httpx
from django.conf import settings
from django.db import DatabaseError, transaction
from django.db.models import F, Q
from django.utils import timezone
from openai import APIStatusError, APITimeoutError

logger = logging.getLogger(__name__)


class PersistError(Exception):
    """A completed AI call could not be saved, so the pipeline must stop.

    The result was paid for but never persisted; continuing would burn more
    quota on results that also cannot be saved.
    """


class TruncatedResponseError(RuntimeError):
    """The provider stopped early with ``finish_reason='length'``.

    Retrying the exact same request with the same output budget will truncate
    again and burn the full output allowance a second time.
    """


# Errors that mean later AI results cannot be persisted either; the pipeline
# should stop instead of paying for more calls.
PERSISTENCE_ERRORS = (DatabaseError, PersistError)


def is_timeout_error(error: Exception) -> bool:
    """True when the request timed out on the client side.

    An upstream non-streaming provider typically keeps generating and billing
    after the client drops the socket, so timeouts must not be immediately
    replayed seconds later in the same run (issue #186).
    """
    return isinstance(error, (APITimeoutError, httpx.TimeoutException, TimeoutError))


def classify_ai_error(error: Exception) -> str:
    """Classify an AI pipeline error into a stable category for state/logs."""
    if is_timeout_error(error):
        return "timeout"
    if isinstance(error, TruncatedResponseError):
        return "truncated"
    if isinstance(error, APIStatusError):
        if error.status_code == 429:
            return "rate_limit"
        if error.status_code < 500:
            return "client_error"
        return "server_error"
    msg = str(error).lower()
    if "no text" in msg or "no complete text response" in msg:
        return "empty_response"
    return "error"


def _should_not_retry(error: Exception) -> bool:
    """Errors for which another attempt cannot help or would re-do paid work.

    * Persistence failures: the database is down, a retry cannot succeed.
    * Chunked-completion failures: the call already split its input and
      retried each chunk; retrying the whole text replays the chunks that
      already succeeded and pays for them again.
    * Client timeouts: upstream generation may still be running and billing;
      immediately replaying in-round stacks concurrent expensive requests.
    * Truncated outputs (finish_reason=length): retrying with identical input
      and output budget deterministically truncates again.
    * Client errors other than timeout/conflict/rate limit.
    """
    if (
        isinstance(error, PERSISTENCE_ERRORS)
        or getattr(error, "is_chunked_failure", False)
        or isinstance(error, TruncatedResponseError)
        or is_timeout_error(error)
    ):
        return True
    return isinstance(error, APIStatusError) and (
        error.status_code < 500 and error.status_code not in (408, 409, 429)
    )


def _has_text(result) -> bool:
    return (
        isinstance(result, dict)
        and isinstance(result.get("text"), str)
        and bool(result["text"].strip())
    )


def auto_retry(
    func: callable, max_retries: int = 3, require_text=False, **kwargs
) -> dict:
    """Try at most max_retries times, propagating failure to the caller.

    Usage paid by failed attempts is not lost: it is added to the successful
    result's ``tokens``, or attached to the final error as ``tokens_used``.
    """
    if max_retries < 1:
        raise ValueError("max_retries must be at least 1")
    spent = 0
    billed_cache_keys = set()
    for attempt in range(max_retries):
        try:
            result = func(**kwargs)
        except Exception as e:
            error = e
            spent += getattr(e, "tokens_used", 0)
            billed_cache_keys.update(getattr(e, "billed_cache_keys", ()) or ())
        else:
            if not require_text or _has_text(result):
                if spent:
                    result["tokens"] = result.get("tokens", 0) + spent
                if billed_cache_keys and isinstance(result, dict):
                    merged_keys = list(billed_cache_keys)
                    merged_keys.extend(result.get("billed_cache_keys") or ())
                    result["billed_cache_keys"] = list(dict.fromkeys(merged_keys))
                return result
            if isinstance(result, dict):
                spent += result.get("tokens", 0)
            error = RuntimeError("Translation or summary returned no text")

        logger.error(f"Attempt {attempt + 1} failed: {error!s}")
        if _should_not_retry(error) or attempt == max_retries - 1:
            if spent:
                error.tokens_used = spent
            if billed_cache_keys:
                error.billed_cache_keys = tuple(billed_cache_keys)
            raise error
        time.sleep(0.5 * (2**attempt))


def extract_content_from_entry(entry_data):
    """Extract content from entry (feedparser)"""
    if "content" in entry_data and entry_data.content:
        for content_item in entry_data.content:
            if content_item.value:
                return content_item.value
    return entry_data.get("summary", "")


def add_tokens(obj, tokens: int) -> None:
    """Atomically add ``tokens`` to ``obj.total_tokens`` (Feed, Digest, ...)."""
    if not tokens:
        return
    type(obj).objects.filter(pk=obj.pk).update(
        total_tokens=F("total_tokens") + tokens
    )
    obj.total_tokens += tokens


def record_spent_tokens(obj, error) -> None:
    """Bill the usage a failed AI call already paid for, if it reported it.

    ``completions()``/``auto_retry()`` attach the spent usage to the raised
    exception as ``tokens_used`` (and any cached chunk keys included in that
    tally as ``billed_cache_keys``). Atomically updates ``obj.total_tokens``
    and zeroes the unbilled balance on those chunk cache rows so retries never
    double-bill while crash recovery still accounts for unbilled chunks.
    Any failure here must not mask the original pipeline error.
    """
    from core.models import TranslationChunkCache

    tokens = getattr(error, "tokens_used", 0)
    billed_cache_keys = tuple(getattr(error, "billed_cache_keys", ()) or ())
    if not tokens and not billed_cache_keys:
        return
    try:
        with transaction.atomic():
            if tokens:
                type(obj).objects.filter(pk=obj.pk).update(
                    total_tokens=F("total_tokens") + tokens
                )
            if billed_cache_keys:
                TranslationChunkCache.objects.filter(
                    cache_key__in=billed_cache_keys
                ).update(tokens=0)
        if tokens:
            obj.total_tokens += tokens
    except Exception:
        logger.warning(
            "Could not record spent tokens for %s %s", type(obj).__name__, obj.pk
        )


def reconcile_completed_entry_task_states(qs) -> int:
    """Repair stale task rows from persisted results, without another AI call.

    Keep the last real attempt timestamp and timeout usage estimates for audit.
    Do not create rows for entries that have never failed, or erase chunk usage.
    """
    from core.models import EntryTaskState

    # Preserve the no-write fast path for healthy feeds and succeeded rows.
    qs = qs.exclude(status=EntryTaskState.STATUS_SUCCEEDED)
    if not qs.exists():
        return 0

    repaired = 0
    for stage, field in (
        ("title", "translated_title"),
        ("content", "translated_content"),
        ("summary", "ai_summary"),
    ):
        completed = qs.filter(stage=stage).exclude(
            **{f"entry__{field}__isnull": True}
        )
        if stage != "summary":
            completed = completed.exclude(**{f"entry__{field}": ""})
        repaired += completed.exclude(status=EntryTaskState.STATUS_SUCCEEDED).update(
            status=EntryTaskState.STATUS_SUCCEEDED,
            attempt_count=0,
            last_error_type="",
            last_error="",
            next_retry_at=None,
            paused_manually=False,
            updated_at=timezone.now(),
        )
    return repaired


def get_entry_task_state_map(feed, stage: str, entry_ids=None) -> dict:
    """Return ``{entry_id: EntryTaskState}`` for existing state rows of a feed."""
    from core.models import EntryTaskState

    if not getattr(feed, "pk", None):
        return {}
    qs = EntryTaskState.objects.filter(entry__feed=feed, stage=stage)
    if entry_ids is not None:
        qs = qs.filter(entry_id__in=entry_ids)
    reconcile_completed_entry_task_states(qs)
    return {state.entry_id: state for state in qs}


def should_skip_entry_stage(state, now=None) -> tuple[bool, str]:
    """Check whether an entry stage should be skipped due to pause or backoff."""
    from core.models import EntryTaskState

    if state is None:
        return False, ""
    if state.status == EntryTaskState.STATUS_PAUSED:
        return True, "paused"
    now = now or timezone.now()
    if (
        state.status == EntryTaskState.STATUS_FAILED
        and state.next_retry_at is not None
        and state.next_retry_at > now
    ):
        return True, "backoff"
    return False, ""


def clear_entry_chunk_caches(entry_ids, stages=None) -> None:
    """Delete persisted chunk caches for the given entries/stages."""
    from core.models import TranslationChunkCache

    if not entry_ids:
        return
    try:
        qs = TranslationChunkCache.objects.filter(entry_id__in=entry_ids)
        if stages is not None:
            qs = qs.filter(stage__in=stages)
        qs.delete()
    except Exception:
        logger.warning("Could not clear chunk caches for entries %s", entry_ids)


def record_entry_stage_success(entry, stage: str) -> None:
    """Mark any existing failure/pause state as succeeded and clean chunk cache."""
    from core.models import EntryTaskState

    if not getattr(entry, "pk", None):
        return
    try:
        EntryTaskState.objects.filter(entry_id=entry.pk, stage=stage).update(
            status=EntryTaskState.STATUS_SUCCEEDED,
            attempt_count=0,
            last_error_type="",
            last_error="",
            next_retry_at=None,
            paused_manually=False,
            last_attempt_at=timezone.now(),
            updated_at=timezone.now(),
        )
    except Exception:
        logger.warning(
            "Could not update EntryTaskState on success for entry %s (%s)",
            entry.pk,
            stage,
        )
    clear_entry_chunk_caches([entry.pk], stages=[stage])


def record_entry_stage_failure(
    entry, stage: str, error: Exception, estimated_input_tokens: int = 0
):
    """Persist failure attempt, exponential backoff, or pause when budget exhausted."""
    from core.models import EntryTaskState

    if not getattr(entry, "pk", None):
        return None

    now = timezone.now()
    error_type = classify_ai_error(error)
    max_attempts = max(1, getattr(settings, "ENTRY_MAX_RETRY_ATTEMPTS", 3))
    base_delay = max(0, getattr(settings, "ENTRY_RETRY_BASE_DELAY_SECONDS", 3600))
    max_delay = max(
        base_delay, getattr(settings, "ENTRY_RETRY_MAX_DELAY_SECONDS", 86400)
    )

    try:
        state, _ = EntryTaskState.objects.get_or_create(
            entry_id=entry.pk,
            stage=stage,
        )
        # If the entry previously succeeded and is failing again (e.g. after a
        # content update), start counting attempts cleanly from 1.
        if state.status == EntryTaskState.STATUS_SUCCEEDED:
            state.attempt_count = 0
        state.attempt_count += 1
        state.last_attempt_at = now
        state.last_error_type = error_type
        state.last_error = str(error)[:2000]
        if error_type == "timeout" and estimated_input_tokens > 0:
            state.estimated_unbilled_tokens += int(estimated_input_tokens)

        # Deterministic truncation (finish_reason=length) after adaptive
        # splitting cannot succeed by replaying identical parameters across
        # hourly cycles; pause immediately instead of burning full output
        # budgets repeatedly.
        if error_type == "truncated" or state.attempt_count >= max_attempts:
            state.status = EntryTaskState.STATUS_PAUSED
            state.next_retry_at = None
        else:
            state.status = EntryTaskState.STATUS_FAILED
            delay = min(
                max_delay, base_delay * (2 ** min(state.attempt_count - 1, 30))
            )
            state.next_retry_at = (
                now + timedelta(seconds=delay) if delay > 0 else now
            )

        state.save()
        return state
    except Exception:
        logger.warning(
            "Could not persist EntryTaskState failure for entry %s (%s)",
            entry.pk,
            stage,
        )
        return None


def reset_entry_task_states_queryset(
    qs,
    *,
    stages=None,
    include_manual_pause: bool = False,
    exclude_succeeded: bool = False,
    clear_last_attempt: bool = False,
) -> int:
    """Reset matching EntryTaskState rows to pending with a fresh retry budget."""
    from core.models import EntryTaskState

    if stages is not None:
        qs = qs.filter(stage__in=stages)
    if not include_manual_pause:
        qs = qs.filter(paused_manually=False)
    # Existing results are authoritative. Resetting a budget is not a force
    # retranslation and must not turn an already completed stage into pending.
    reconcile_completed_entry_task_states(qs)
    if exclude_succeeded:
        qs = qs.exclude(status=EntryTaskState.STATUS_SUCCEEDED)
    else:
        # A succeeded row whose result was explicitly cleared must be reset.
        for stage, field in (
            ("title", "translated_title"),
            ("content", "translated_content"),
            ("summary", "ai_summary"),
        ):
            completed = Q(stage=stage) & Q(**{f"entry__{field}__isnull": False})
            if stage != "summary":
                completed &= ~Q(**{f"entry__{field}": ""})
            qs = qs.exclude(completed)
    updates = {
        "status": EntryTaskState.STATUS_PENDING,
        "attempt_count": 0,
        "last_error_type": "",
        "last_error": "",
        "next_retry_at": None,
        "paused_manually": False,
        "updated_at": timezone.now(),
    }
    if clear_last_attempt:
        updates["last_attempt_at"] = None
    return qs.update(**updates)


def reset_entry_task_states(
    entry_ids, stages=None, include_manual_pause: bool = False, clear_chunks: bool = True
) -> None:
    """Reset retry budgets/backoff for entries (e.g. on source change or admin action)."""
    from core.models import EntryTaskState

    if not entry_ids:
        return
    try:
        reset_entry_task_states_queryset(
            EntryTaskState.objects.filter(entry_id__in=entry_ids),
            stages=stages,
            include_manual_pause=include_manual_pause,
            clear_last_attempt=clear_chunks,
        )
    except Exception:
        logger.warning("Could not reset EntryTaskState for entries %s", entry_ids)

    if clear_chunks:
        clear_entry_chunk_caches(entry_ids, stages=stages)

import logging
import uuid
from django.utils import timezone
from django.db import transaction
from django.db.models import F

from core.models import Feed, Entry, Agent
from utils import text_handler
from core.tasks.utils import (
    PERSISTENCE_ERRORS,
    PersistError,
    auto_retry,
    get_entry_task_state_map,
    is_timeout_error,
    record_entry_stage_failure,
    record_entry_stage_success,
    record_spent_tokens,
    should_skip_entry_stage,
)

logger = logging.getLogger(__name__)


def handle_feeds_summary(feeds: list):
    for feed in feeds:
        try:
            if not feed.entries.exists():
                continue

            feed.translation_status = None
            feed.translation_started_at = timezone.now()
            feed.save(
                update_fields=["translation_status", "translation_started_at"]
            )
            logger.info(
                "Start summary feed %s to %s", feed.feed_url, feed.target_language
            )
            if not feed.summarizer:
                raise Exception("Summarizer Engine Not Set")

            min_chunk_size = feed.summarizer.min_size()
            max_chunk_size = feed.summarizer.max_size()
            max_context_tokens = feed.summarizer.max_tokens

            completed = summarize_feed(
                feed,
                min_chunk_size=min_chunk_size,
                max_chunk_size=max_chunk_size,
                max_context_tokens=max_context_tokens,
            )
            feed.translation_status = completed
            feed.translation_started_at = None
            message = "Summary Completed" if completed else "Summary incomplete; failed entries remain pending"
            feed.log += f"{timezone.now()} {message} <br>"
        except Exception as e:
            logger.error(f"Error in summarize_feed for feed {feed.name}: {str(e)}")
            feed.translation_status = False
            feed.translation_started_at = None
            feed.log += f"{timezone.now()} {str(e)}<br>"
        finally:
            # Explicitly clean up reference
            del feed

    Feed.objects.bulk_update(
        feeds, fields=["translation_status", "translation_started_at", "log"]
    )
    # Clear the list after processing
    del feeds


def summarize_feed(
    feed: Feed,
    min_chunk_size: int = 300,
    max_chunk_size: int = 1500,
    summarize_recursively: bool = True,
    max_context_chunks: int = 4,
    max_context_tokens: int = 3000,
    chunk_delimiter: str = ".",
    max_chunks_per_entry: int = 20,
    summarizer: Agent = None,
    entry_ids: list | None = None,
):
    """
    Generate content summary with memory optimizations.
    """
    summarizer = summarizer or feed.summarizer
    # Legacy rows can carry NULL or out-of-range values; clamp instead of
    # failing every summary for this feed.
    summary_detail = min(1.0, max(0.0, feed.summary_detail or 0.0))
    completed = True
    entries = None

    try:
        state_map = get_entry_task_state_map(feed, "summary", entry_ids=entry_ids)
        now = timezone.now()

        # 只处理前 feed.max_posts 条 entries; -id tie-break matches the
        # cleanup keep-set so equal pubdates can't diverge the window.
        window_qs = (
            feed.entries.select_related("feed")
            .filter(ai_summary__isnull=True)
            .order_by("-pubdate", "-id")
        )
        if entry_ids is not None:
            window_qs = window_qs.filter(pk__in=entry_ids)
        else:
            window_qs = window_qs[: feed.max_posts]
        total_entries = window_qs.count()
        if not total_entries:
            logger.info(f"No entries to summarize for feed: {feed.feed_url}")
            return True
        entries = window_qs.iterator(chunk_size=30)

        logger.info(
            f"Starting summary for {total_entries} entries in feed: {feed.feed_url}"
        )

        for idx, entry in enumerate(entries):
            attempt_id = uuid.uuid4().hex[:12]
            try:
                entry_state = state_map.get(entry.pk)
                skip, skip_reason = should_skip_entry_stage(entry_state, now=now)
                if skip:
                    if skip_reason == "backoff":
                        # Still failing and awaiting retry (not paused/isolated),
                        # so summary for this feed is not yet complete.
                        completed = False
                    logger.info(
                        "Skipping summary for entry %s (%s, attempts=%s, next_retry_at=%s)",
                        entry.pk,
                        skip_reason,
                        entry_state.attempt_count,
                        entry_state.next_retry_at,
                    )
                    continue

                logger.info(
                    f"[{idx + 1}/{total_entries}] Processing: {entry.original_title}"
                )

                # Use the extracted function for single entry processing
                billed_cache_keys: list[str] = []
                summary, entry_tokens = _summarize_entry(
                    entry=entry,
                    summarizer=summarizer,
                    target_language=feed.target_language,
                    min_chunk_size=min_chunk_size,
                    max_chunk_size=max_chunk_size,
                    summarize_recursively=summarize_recursively,
                    max_context_chunks=max_context_chunks,
                    max_context_tokens=max_context_tokens,
                    chunk_delimiter=chunk_delimiter,
                    max_chunks_per_entry=max_chunks_per_entry,
                    summary_detail=summary_detail,
                    user_prompt=feed.additional_prompt,
                    attempt_id=attempt_id,
                    billed_cache_keys_out=billed_cache_keys,
                )

                entry.ai_summary = summary
                _save_progress(
                    [entry],
                    feed,
                    entry_tokens,
                    billed_cache_keys=billed_cache_keys,
                )
                record_entry_stage_success(entry, "summary")

                logger.info(
                    f"Completed summary for '{entry.original_title}' - Tokens: {entry_tokens}"
                )

            except PERSISTENCE_ERRORS as e:
                # The summary was generated but could not be saved; further
                # entries would fail the same way, so stop paying for more.
                record_spent_tokens(feed, e)
                logger.error(
                    f"Aborting summary for feed {feed.feed_url} at entry {entry.original_title}: {e}"
                )
                completed = False
                feed.log += f"{timezone.now()} Persistence failure, summary aborted at entry {entry.pk}: {e}<br>"
                break
            except Exception as e:
                record_spent_tokens(feed, e)
                est_tokens = 0
                if is_timeout_error(e):
                    est_tokens = getattr(e, "estimated_unbilled_tokens", 0)
                    if not est_tokens:
                        try:
                            est_tokens = text_handler.get_token_count(
                                text_handler.clean_content(entry.original_content)
                            )
                        except Exception:
                            est_tokens = 0
                state = record_entry_stage_failure(
                    entry,
                    "summary",
                    e,
                    estimated_input_tokens=est_tokens,
                )
                logger.error(
                    "Error summarizing entry %s (%s) attempt_id=%s error_type=%s "
                    "attempts=%s status=%s next_retry_at=%s: %s",
                    entry.pk,
                    entry.original_title,
                    attempt_id,
                    state.last_error_type if state else "unknown",
                    state.attempt_count if state else None,
                    state.status if state else None,
                    state.next_retry_at if state else None,
                    str(e),
                )
                completed = False
                feed.log += f"{timezone.now()} Summary failed for entry {entry.pk}: {str(e)}<br>"
    except Exception as e:
        logger.error(f"Critical error summarizing feed {feed.feed_url}")
        feed.log += f"{timezone.now()} Critical error: {str(e)}<br>"
        completed = False
    finally:
        logger.info(f"Completed summary process for feed: {feed.feed_url}")
    return completed


def _summarize_entry(
    entry: Entry,
    summarizer: Agent,
    target_language: str,
    min_chunk_size: int,
    max_chunk_size: int,
    summarize_recursively: bool,
    max_context_chunks: int,
    max_context_tokens: int,
    chunk_delimiter: str,
    max_chunks_per_entry: int,
    summary_detail: float,
    user_prompt: str = None,
    attempt_id: str | None = None,
    billed_cache_keys_out: list | None = None,
) -> tuple[str, int]:
    """
    Summarize a single entry with memory optimizations.

    Returns:
        tuple: (summary_text, tokens_used)
    """
    # Clean and prepare content
    content_text = text_handler.clean_content(entry.original_content)

    # Skip empty content
    if not content_text.strip():
        return "[No content available]", 0

    # Clamp chunk sizes so an undetected/zeroed agent limit cannot divide
    # by zero or produce unbounded chunks; sane defaults keep the pipeline
    # running while the model's real limit is being detected.
    min_chunk_size = max(1, int(min_chunk_size or 300))
    max_chunk_size = max(min_chunk_size, int(max_chunk_size or 1500))
    max_context_tokens = max_context_tokens or 3000
    effective_limit = (
        summarizer.effective_chunk_limit()
        if hasattr(summarizer, "effective_chunk_limit")
        else 0
    )
    if (
        isinstance(effective_limit, (int, float))
        and effective_limit > 0
        and max_context_tokens > effective_limit
    ):
        # Keep recursive context + chunk within the agent's effective chunk
        # budget so completions() does not re-split the prompt without context.
        max_context_tokens = min(
            max_context_tokens, max(200, int(effective_limit) - max_chunk_size)
        )

    # Calculate chunking parameters
    token_count = text_handler.get_token_count(content_text)
    min_chunks = 1
    max_chunks = max(1, min(max_chunks_per_entry, token_count // min_chunk_size))
    target_chunks = max(
        1,
        min(
            max_chunks,
            int(min_chunks + summary_detail * (max_chunks - min_chunks)),
        ),
    )

    # Generate chunks
    text_chunks = text_handler.adaptive_chunking(
        content_text,
        target_chunks=target_chunks,
        min_chunk_size=min_chunk_size,
        max_chunk_size=max_chunk_size,
        initial_delimiter=chunk_delimiter,
    )

    # Clean up original content
    del content_text

    actual_chunks = len(text_chunks)
    entry_pk = getattr(entry, "pk", None)

    # Handle small content directly
    if actual_chunks == 1:
        extra_kwargs = {}
        if entry_pk is not None:
            single_ctx = {
                "entry_id": entry_pk,
                "stage": "summary",
                "target_language": target_language,
            }
            if attempt_id:
                single_ctx["attempt_id"] = attempt_id
            extra_kwargs["chunk_cache_context"] = single_ctx
        response = auto_retry(
            summarizer.summarize,
            max_retries=3,
            require_text=True,
            text=text_chunks[0],
            target_language=target_language,
            user_prompt=user_prompt,
            **extra_kwargs,
        )
        summary = response.get("text", "")
        tokens = response.get("tokens", 0)
        if billed_cache_keys_out is not None and response.get("billed_cache_keys"):
            billed_cache_keys_out.extend(response["billed_cache_keys"])
        del text_chunks
        return summary, tokens

    # Process chunks with context management and per-chunk resume caching
    accumulated_summaries = []
    total_tokens = 0
    billed_cache_keys = []
    multi_chunk_cache_context = None
    if entry_pk is not None:
        multi_chunk_cache_context = {
            "entry_id": entry_pk,
            "stage": "summary",
            "target_language": target_language,
            "cache_leaf": True,
        }
        if attempt_id:
            multi_chunk_cache_context["attempt_id"] = attempt_id

    for chunk_idx, chunk in enumerate(text_chunks):
        # Prepare context
        context_parts = []
        if summarize_recursively and accumulated_summaries:
            context_token_count = 0
            context_candidates = accumulated_summaries[-max_context_chunks:]

            for summary in reversed(context_candidates):
                summary_tokens = text_handler.get_token_count(summary)
                if context_token_count + summary_tokens <= max_context_tokens:
                    context_parts.insert(0, summary)
                    context_token_count += summary_tokens
                else:
                    break

        # Construct prompt
        prompt = (
            "\n\n".join(context_parts) + "\n\nCurrent text to summarize:\n\n" + chunk
            if context_parts
            else chunk
        )

        # Summarize with retry
        extra_kwargs = (
            {"chunk_cache_context": multi_chunk_cache_context}
            if multi_chunk_cache_context
            else {}
        )
        try:
            response = auto_retry(
                summarizer.summarize,
                max_retries=3,
                require_text=True,
                text=prompt,
                target_language=target_language,
                user_prompt=user_prompt,
                **extra_kwargs,
            )
        except Exception as e:
            # Earlier chunks were already paid for; let the caller bill them
            # and zero out their unbilled cache balances atomically.
            e.tokens_used = total_tokens + getattr(e, "tokens_used", 0)
            merged_keys = list(billed_cache_keys)
            merged_keys.extend(getattr(e, "billed_cache_keys", ()) or ())
            if merged_keys:
                e.billed_cache_keys = tuple(dict.fromkeys(merged_keys))
            raise

        chunk_summary = response.get("text", "")
        accumulated_summaries.append(chunk_summary)
        total_tokens += response.get("tokens", 0)
        if response.get("billed_cache_keys"):
            billed_cache_keys.extend(response["billed_cache_keys"])

        # Clean up
        del prompt, response
        # Release the processed text without shifting the remaining chunks.
        text_chunks[chunk_idx] = ""

    # Finalize summary
    final_summary = "\n\n".join(accumulated_summaries)
    del accumulated_summaries

    if billed_cache_keys_out is not None and billed_cache_keys:
        billed_cache_keys_out.extend(dict.fromkeys(billed_cache_keys))

    return final_summary, total_tokens


def _save_progress(entries_to_save, feed, total_tokens, billed_cache_keys=()):
    """Commit completed summaries and their usage together."""
    try:
        with transaction.atomic():
            if entries_to_save:
                Entry.objects.bulk_update(entries_to_save, fields=["ai_summary"])
            if total_tokens > 0:
                Feed.objects.filter(pk=feed.pk).update(total_tokens=F("total_tokens") + total_tokens)
    except Exception as save_error:
        # Nothing was committed, so the paid usage rides on the error and
        # the caller bills it via record_spent_tokens().
        error = PersistError(f"Could not persist summaries: {save_error}")
        error.tokens_used = total_tokens
        if billed_cache_keys:
            error.billed_cache_keys = tuple(dict.fromkeys(billed_cache_keys))
        raise error from save_error
    if total_tokens > 0:
        feed.total_tokens += total_tokens

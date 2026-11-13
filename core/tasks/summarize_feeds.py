import logging
from django.utils import timezone
from django.db import transaction
from django.db.models import F

from core.models import Feed, Entry, Agent
from utils import text_handler
from core.tasks.utils import (
    PERSISTENCE_ERRORS,
    PersistError,
    auto_retry,
    record_spent_tokens,
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
        # 只处理前 feed.max_posts 条 entries
        entries = (
            feed.entries.select_related("feed")
            .filter(ai_summary__isnull=True)
            .order_by("-pubdate")[: feed.max_posts]
            .iterator(chunk_size=30)
        )
        total_entries = (
            feed.entries.filter(ai_summary__isnull=True)
            .order_by("-pubdate")[: feed.max_posts]
            .count()
        )
        if not total_entries:
            logger.info(f"No entries to summarize for feed: {feed.feed_url}")
            return True

        logger.info(
            f"Starting summary for {total_entries} entries in feed: {feed.feed_url}"
        )

        for idx, entry in enumerate(entries):
            try:
                logger.info(
                    f"[{idx + 1}/{total_entries}] Processing: {entry.original_title}"
                )

                # Use the extracted function for single entry processing
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
                )

                entry.ai_summary = summary
                _save_progress([entry], feed, entry_tokens)

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
                logger.error(
                    f"Error summarizing entry {entry.original_title}: {str(e)}"
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

    # Handle small content directly
    if actual_chunks == 1:
        response = auto_retry(
            summarizer.summarize,
            max_retries=3,
            require_text=True,
            text=text_chunks[0],
            target_language=target_language,
            user_prompt=user_prompt,
        )
        summary = response.get("text", "")
        tokens = response.get("tokens", 0)
        del text_chunks
        return summary, tokens

    # Process chunks with context management
    accumulated_summaries = []
    total_tokens = 0

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
        try:
            response = auto_retry(
                summarizer.summarize,
                max_retries=3,
                require_text=True,
                text=prompt,
                target_language=target_language,
                user_prompt=user_prompt,
            )
        except Exception as e:
            # Earlier chunks were already paid for; let the caller bill them.
            e.tokens_used = total_tokens + getattr(e, "tokens_used", 0)
            raise

        chunk_summary = response.get("text", "")
        accumulated_summaries.append(chunk_summary)
        total_tokens += response.get("tokens", 0)

        # Clean up
        del prompt, response
        # Release the processed text without shifting the remaining chunks.
        text_chunks[chunk_idx] = ""

    # Finalize summary
    final_summary = "\n\n".join(accumulated_summaries)
    del accumulated_summaries

    return final_summary, total_tokens


def _save_progress(entries_to_save, feed, total_tokens):
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
        raise error from save_error
    if total_tokens > 0:
        feed.total_tokens += total_tokens

import logging
from django.utils import timezone

from core.models import Feed, Entry, Agent
from utils import text_handler
from core.tasks.utils import auto_retry

logger = logging.getLogger(__name__)


def handle_feeds_summary(feeds: list):
    for feed in feeds:
        try:
            if not feed.entries.exists():
                continue

            feed.translation_status = None
            feed.save()
            logger.info(
                "Start summary feed %s to %s", feed.feed_url, feed.target_language
            )
            if not feed.summarizer:
                raise Exception("Summarizer Engine Not Set")

            min_chunk_size = feed.summarizer.min_size()
            max_chunk_size = feed.summarizer.max_size()
            max_context_tokens = feed.summarizer.max_tokens

            summarize_feed(
                feed,
                min_chunk_size=min_chunk_size,
                max_chunk_size=max_chunk_size,
                max_context_tokens=max_context_tokens,
            )
            feed.translation_status = True
            feed.log += f"{timezone.now()} Summary Completed <br>"
        except Exception as e:
            logger.error(f"Error in summarize_feed for feed {feed.name}: {str(e)}")
            feed.translation_status = False
            feed.log += f"{timezone.now()} {str(e)}<br>"
        finally:
            # Explicitly clean up reference
            del feed

    Feed.objects.bulk_update(
        feeds, fields=["translation_status", "log", "total_tokens", "total_characters"]
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
    assert 0 <= feed.summary_detail <= 1, "summary_detail must be between 0 and 1"
    entries_to_save = []
    total_tokens = 0
    BATCH_SIZE = 5  # Reduced batch size for memory efficiency

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
            return False

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
                    summary_detail=feed.summary_detail,
                )

                entry.ai_summary = summary
                total_tokens += entry_tokens
                entries_to_save.append(entry)

                logger.info(
                    f"Completed summary for '{entry.original_title}' - Tokens: {entry_tokens}"
                )

                # Periodically save progress with smaller batch size
                if len(entries_to_save) >= BATCH_SIZE:
                    _save_progress(entries_to_save, feed, total_tokens)
                    total_tokens = 0
                    entries_to_save = []

                    # Force garbage collection
                    import gc

                    gc.collect()

            except Exception as e:
                logger.error(
                    f"Error summarizing entry {entry.original_title}: {str(e)}"
                )
                entry.ai_summary = f"[Summary failed: {str(e)}]"
                entries_to_save.append(entry)
    except Exception as e:
        logger.error(f"Critical error summarizing feed {feed.feed_url}")
        feed.log += f"{timezone.now()} Critical error: {str(e)}<br>"
    finally:
        if not summarizer:
            _save_progress(entries_to_save, feed, total_tokens)
        else:
            _save_progress(entries_to_save, feed, 0)
        logger.info(f"Completed summary process for feed: {feed.feed_url}")

        # Clean up large references
        del entries, entries_to_save
    return True


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
            text=text_chunks[0],
            target_language=target_language,
        )
        summary = response.get("text", "")
        tokens = response.get("tokens", 0)
        del text_chunks
        return summary, tokens

    # Process chunks with context management
    accumulated_summaries = []
    context_token_count = 0
    total_tokens = 0

    for chunk_idx, chunk in enumerate(text_chunks):
        # Prepare context
        context_parts = []
        if summarize_recursively and accumulated_summaries:
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
        response = auto_retry(
            summarizer.summarize,
            max_retries=3,
            text=prompt,
            target_language=target_language,
            max_tokens=max_context_tokens,
        )

        chunk_summary = response.get("text", "")
        accumulated_summaries.append(chunk_summary)
        total_tokens += response.get("tokens", 0)
        context_token_count = text_handler.get_token_count(chunk_summary)

        # Clean up
        del prompt, response
        del text_chunks[chunk_idx]

    # Finalize summary
    final_summary = "\n\n".join(accumulated_summaries)
    del accumulated_summaries

    return final_summary, total_tokens


def _save_progress(entries_to_save, feed, total_tokens):
    """Save progress with memory cleanup."""
    if entries_to_save:
        Entry.objects.bulk_update(entries_to_save, fields=["ai_summary"])
        del entries_to_save

    if total_tokens > 0:
        feed.total_tokens += total_tokens
        feed.save()

import logging
import time

from django.db import DatabaseError
from openai import APIStatusError

logger = logging.getLogger(__name__)


class PersistError(Exception):
    """A completed AI call could not be saved, so the pipeline must stop.

    The result was paid for but never persisted; continuing would burn more
    quota on results that also cannot be saved.
    """


# Errors that mean later AI results cannot be persisted either; the pipeline
# should stop instead of paying for more calls.
PERSISTENCE_ERRORS = (DatabaseError, PersistError)


def _should_not_retry(error: Exception) -> bool:
    """Errors for which another attempt would re-do paid work.

    * Persistence failures: the database is down, a retry cannot succeed.
    * Chunked-completion failures: the call already split its input and
      retried each chunk; retrying the whole text replays the chunks that
      already succeeded and pays for them again.
    """
    return isinstance(error, PERSISTENCE_ERRORS) or getattr(
        error, "is_chunked_failure", False
    )


def auto_retry(
    func: callable, max_retries: int = 3, require_text=False, **kwargs
) -> dict:
    """Try at most max_retries times, propagating failure to the caller."""
    if max_retries < 1:
        raise ValueError("max_retries must be at least 1")
    for attempt in range(max_retries):
        try:
            result = func(**kwargs)
            if require_text and (
                not isinstance(result, dict)
                or not isinstance(result.get("text"), str)
                or not result["text"].strip()
            ):
                raise RuntimeError("Translation or summary returned no text")
            return result
        except Exception as e:
            logger.error(f"Attempt {attempt + 1} failed: {e!s}")
            # Failures that would re-do already-paid work are not retried.
            if _should_not_retry(e):
                raise
            if isinstance(e, APIStatusError) and (
                e.status_code < 500 and e.status_code not in (408, 409, 429)
            ):
                raise
            if attempt == max_retries - 1:
                raise
            time.sleep(0.5 * (2**attempt))


def extract_content_from_entry(entry_data):
    """Extract content from entry (feedparser)"""
    if "content" in entry_data and entry_data.content:
        for content_item in entry_data.content:
            if content_item.value:
                return content_item.value
    return entry_data.get("summary", "")


def record_spent_tokens(feed, error) -> None:
    """Bill the usage a failed AI call already paid for, if it reported it.

    ``completions()`` attaches the spent usage to the raised exception as
    ``tokens_used``; persist it so failed attempts are not lost from the
    feed's token accounting. Any failure here must not mask the original
    pipeline error.
    """
    spent = getattr(error, "tokens_used", 0)
    if not spent:
        return
    try:
        from django.db.models import F
        from core.models.feed import Feed

        Feed.objects.filter(pk=feed.pk).update(
            total_tokens=F("total_tokens") + spent
        )
        feed.total_tokens += spent
    except Exception:
        logger.warning("Could not record spent tokens for feed %s", feed.pk)

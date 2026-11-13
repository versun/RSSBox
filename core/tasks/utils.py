import logging
import time

from django.db import DatabaseError
from django.db.models import F
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
    """Errors for which another attempt cannot help or would re-do paid work.

    * Persistence failures: the database is down, a retry cannot succeed.
    * Chunked-completion failures: the call already split its input and
      retried each chunk; retrying the whole text replays the chunks that
      already succeeded and pays for them again.
    * Client errors other than timeout/conflict/rate limit.
    """
    if isinstance(error, PERSISTENCE_ERRORS) or getattr(
        error, "is_chunked_failure", False
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
    for attempt in range(max_retries):
        try:
            result = func(**kwargs)
        except Exception as e:
            error = e
            spent += getattr(e, "tokens_used", 0)
        else:
            if not require_text or _has_text(result):
                if spent:
                    result["tokens"] = result.get("tokens", 0) + spent
                return result
            if isinstance(result, dict):
                spent += result.get("tokens", 0)
            error = RuntimeError("Translation or summary returned no text")

        if spent:
            error.tokens_used = spent
        logger.error(f"Attempt {attempt + 1} failed: {error!s}")
        if _should_not_retry(error) or attempt == max_retries - 1:
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
    exception as ``tokens_used``. Any failure here must not mask the original
    pipeline error.
    """
    try:
        add_tokens(obj, getattr(error, "tokens_used", 0))
    except Exception:
        logger.warning(
            "Could not record spent tokens for %s %s", type(obj).__name__, obj.pk
        )

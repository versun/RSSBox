import logging
import time

from openai import APIStatusError

logger = logging.getLogger(__name__)


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

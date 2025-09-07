import logging
import time

logger = logging.getLogger(__name__)


def auto_retry(func: callable, max_retries: int = 3, **kwargs) -> dict:
    """Retry function with exponential backoff and memory cleanup."""
    result = {}
    for attempt in range(max_retries):
        try:
            result = func(**kwargs)
            break
        except Exception as e:
            logger.error(f"Attempt {attempt + 1} failed: {str(e)}")
            time.sleep(0.5 * (2**attempt))  # Exponential backoff

    # Clean up kwargs if they contain large objects
    for key in list(kwargs.keys()):
        if key == "text" and isinstance(kwargs[key], str) and len(kwargs[key]) > 1000:
            del kwargs[key]

    return result


def extract_content_from_entry(entry_data):
    """Extract content from entry (feedparser)"""
    if "content" in entry_data and entry_data.content:
        for content_item in entry_data.content:
            if content_item.value:
                return content_item.value
    return entry_data.get("summary", "")

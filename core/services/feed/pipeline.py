import logging

from django.db import close_old_connections

from core.models import Feed
from core.tasks.fetch_feeds import handle_single_feed_fetch
from core.tasks.translate_feeds import handle_feeds_translation
from core.tasks.summarize_feeds import handle_feeds_summary

logger = logging.getLogger(__name__)


def run_feed_update(
    feed: Feed,
    *,
    fetch_func=None,
    translate_func=None,
    summarize_func=None,
    close_connections=None,
    pipeline_logger=None,
) -> bool:
    fetch_func = fetch_func or handle_single_feed_fetch
    translate_func = translate_func or handle_feeds_translation
    summarize_func = summarize_func or handle_feeds_summary
    close_connections = close_connections or close_old_connections
    pipeline_logger = pipeline_logger or logger

    try:
        close_connections()
        try:
            pipeline_logger.info(f"Starting feed update: {feed.name}")

            fetch_func(feed)
            if feed.translate_title:
                translate_func([feed], target_field="title")
            if feed.translate_content:
                translate_func([feed], target_field="content")
            if feed.summary:
                summarize_func([feed])

            pipeline_logger.info(f"Completed feed update: {feed.name}")
            return True
        except Feed.DoesNotExist:
            pipeline_logger.error(f"Feed not found: ID {feed.name}")
            return False
        except Exception as exc:
            pipeline_logger.exception(f"Error updating feed ID {feed.name}: {str(exc)}")
            return False
    finally:
        close_connections()

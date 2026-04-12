from itertools import chain
import logging
import time

from core.models import Tag
from core.cache import cache_rss, cache_tag

logger = logging.getLogger(__name__)


def refresh_feed_caches(
    feeds,
    *,
    cache_rss_func=None,
    logger=None,
    time_func=None,
):
    cache_rss_func = cache_rss_func or cache_rss
    logger = logger or logging.getLogger(__name__)
    time_func = time_func or time.time

    for feed in feeds:
        try:
            cache_rss_func(feed.slug, feed_type="o", format="xml")
            cache_rss_func(feed.slug, feed_type="o", format="json")
            cache_rss_func(feed.slug, feed_type="t", format="xml")
            cache_rss_func(feed.slug, feed_type="t", format="json")
        except Exception as exc:
            logger.error(
                f"{time_func()}: Failed to cache RSS for {feed.slug}: {str(exc)}"
            )


def get_related_tags(feeds, *, tag_model=None):
    tag_model = tag_model or Tag
    tag_ids = set(
        chain.from_iterable(feed.tags.values_list("id", flat=True) for feed in feeds)
    )
    return tag_model.objects.filter(id__in=tag_ids)


def refresh_tag_caches(tags, *, cache_tag_func=None, logger=None):
    cache_tag_func = cache_tag_func or cache_tag
    logger = logger or logging.getLogger(__name__)

    for tag in tags:
        try:
            cache_tag_func(tag.slug, feed_type="o", format="xml")
            cache_tag_func(tag.slug, feed_type="t", format="xml")
            cache_tag_func(tag.slug, feed_type="t", format="json")
        except Exception as exc:
            logger.error(f"Failed to cache tag {tag.slug}: {str(exc)}")


def refresh_updated_content(
    feeds,
    *,
    tag_model=None,
    cache_rss_func=None,
    cache_tag_func=None,
    logger=None,
    time_func=None,
):
    logger = logger or logging.getLogger(__name__)
    refresh_feed_caches(
        feeds,
        cache_rss_func=cache_rss_func,
        logger=logger,
        time_func=time_func,
    )
    tags = get_related_tags(feeds, tag_model=tag_model)
    refresh_tag_caches(
        tags,
        cache_tag_func=cache_tag_func,
        logger=logger,
    )

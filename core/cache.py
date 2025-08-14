import logging
from django.core.cache import cache
from utils.feed_action import merge_feeds_into_one_atom, generate_atom_feed

from .models import Feed

logger = logging.getLogger(__name__)


def cache_rss(feed_slug: str, feed_type="t", format="xml"):
    logger.debug(
        f"Start cache_rss for {feed_slug} with type {feed_type} and format {format}"
    )
    # 生成唯一的缓存键
    cache_key = f"cache_rss_{feed_slug}_{feed_type}_{format}"

    feed = Feed.objects.get(slug=feed_slug)
    atom_feed = generate_atom_feed(feed, feed_type)
    if not atom_feed:
        return None

    # 缓存
    cache.set(cache_key, atom_feed, feed.update_frequency or 86400)  # default to 1 day
    logger.debug(f"Cached successfully with key {cache_key}")
    return atom_feed


def cache_tag(tag: str, feed_type="t", format="xml"):
    logger.debug(f"Start cache_tag for {tag} with type {feed_type} and format {format}")
    # 生成唯一的缓存键
    cache_key = f"cache_tag_{tag}_{feed_type}_{format}"

    feeds = Feed.objects.filter(tags__name=tag)
    max_frequency_feed = feeds.order_by("-update_frequency").first()
    atom_feed = merge_feeds_into_one_atom(tag, feeds, feed_type)

    if not atom_feed:
        return None

    # 缓存
    max_frequency = max_frequency_feed.update_frequency if max_frequency_feed else 86400
    cache.set(cache_key, atom_feed, max_frequency)
    logger.debug(f"Cached successfully with key {cache_key}")
    return atom_feed

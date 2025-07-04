import logging
from django.core.cache import cache
from utils.feed_action import merge_feeds_into_one_atom, generate_atom_feed

from .models import Feed


def cache_rss(feed_slug: str, feed_type="t", format="xml"):
    logging.debug(
        f"Start cache_rss for {feed_slug} with type {feed_type} and format {format}"
    )
    # 生成唯一的缓存键
    cache_key = f"cache_rss_{feed_slug}_{feed_type}_{format}"

    feed = Feed.objects.get(slug=feed_slug)
    atom_feed = generate_atom_feed(feed, feed_type)
    if not atom_feed:
        return None

    # 缓存
    cache.set(cache_key, atom_feed, feed.update_frequency or 86400) # default to 1 day
    logging.debug(f"Cached successfully with key {cache_key}")
    return atom_feed


def cache_category(category: str, feed_type="t", format="xml"):
    logging.debug(
        f"Start cache_category for {category} with type {feed_type} and format {format}"
    )
    # 生成唯一的缓存键
    cache_key = f"cache_category_{category}_{feed_type}_{format}"

    feeds = Feed.objects.filter(category=category)
    max_frequency_feed = feeds.order_by('-update_frequency').first()
    atom_feed = merge_feeds_into_one_atom(category, feeds, feed_type)

    if not atom_feed:
        return None

    # 缓存
    cache.set(cache_key, atom_feed, max_frequency_feed.update_frequency or 86400)
    logging.debug(f"Cached successfully with key {cache_key}")
    return atom_feed

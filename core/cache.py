import logging
from django.core.cache import cache
from core.models import Feed
from utils.text_handler import set_translation_display

from core.services.feed.rendering import (
    add_atom_entry as service_add_atom_entry,
    build_atom_feed as service_build_atom_feed,
    finalize_atom_feed as service_finalize_atom_feed,
    render_feed_content,
    render_tag_content,
)

logger = logging.getLogger(__name__)


def cache_rss(feed_slug: str, feed_type="t", format="xml"):
    logger.debug(
        f"Start cache_rss for {feed_slug} with type {feed_type} and format {format}"
    )
    # 生成唯一的缓存键
    cache_key = f"cache_rss_{feed_slug}_{feed_type}_{format}"

    feed = Feed.objects.get(slug=feed_slug)
    # 如果请求翻译版本，检查翻译状态  
    if feed_type == "t" and (feed.translate_title or feed.translate_content or feed.summary):  
        if feed.translation_status is None:  
            # 翻译正在进行中，不更新缓存  
            logger.debug(f"Translation in progress for {feed_slug}, using old cache if available")  
            existing_cache = cache.get(cache_key)  
            return existing_cache  # 返回旧缓存或 None  
        elif feed.translation_status is False:  
            # 翻译失败，记录警告但仍然生成 feed（包含原文）  
            logger.warning(f"Translation failed for {feed_slug}, generating feed with original content")
              
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


def _build_atom_feed(*args, **kwargs):
    return service_build_atom_feed(*args, **kwargs)


def _add_atom_entry(*args, **kwargs):
    return service_add_atom_entry(*args, **kwargs)


def generate_atom_feed(feed: Feed, feed_type="t"):
    return render_feed_content(
        feed,
        feed_type=feed_type,
        build_feed_func=_build_atom_feed,
        add_entry_func=_add_atom_entry,
        finalize_func=_finalize_atom_feed,
        render_logger=logger,
    )


def merge_feeds_into_one_atom(tag: str, feeds: list[Feed], feed_type="t"):
    return render_tag_content(
        tag,
        feeds,
        feed_type=feed_type,
        build_feed_func=_build_atom_feed,
        add_entry_func=_add_atom_entry,
        finalize_func=_finalize_atom_feed,
    )


def _finalize_atom_feed(fg):
    return service_finalize_atom_feed(fg)

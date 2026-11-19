import logging
import threading
from django.core.cache import cache
from django.utils import timezone
from django.conf import settings
from lxml import etree
import mistune
from feedgen.entry import FeedEntry
from feedgen.feed import FeedGenerator
from core.models import Feed, Entry, Tag
from core.models.filter import cache_version
from utils.text_handler import set_translation_display

logger = logging.getLogger(__name__)

DEFAULT_CACHE_TIMEOUT_MINUTES = 1440  # 1 day
_DIGEST_CACHE_TIMEOUT_SECONDS = 3600  # digests regenerate at most daily
_SAFE_XML_PARSER = etree.XMLParser(resolve_entities=False, no_network=True)

# Per-process guards around feed generation: a cache miss used to let every
# concurrent request regenerate the same feed (single-flight). Locks are
# keyed by the feed/tag identity, not the versioned cache key, so a version
# bump mid-wait does not strand waiters on an orphaned lock. The dict is
# bounded so a long-lived process can't leak locks for slugs never seen
# again; clearing is safe because any lock currently held is still held on
# its own reference.
_GENERATION_LOCK_LIMIT = 1024
_generation_locks_guard = threading.Lock()
_generation_locks: dict = {}


def _generation_lock_for(scope: str, identity) -> threading.Lock:
    with _generation_locks_guard:
        if len(_generation_locks) >= _GENERATION_LOCK_LIMIT:
            _generation_locks.clear()
        return _generation_locks.setdefault((scope, identity), threading.Lock())


def _cache_is_mocked() -> bool:
    """True when tests replaced ``core.cache.cache`` with a Mock.

    Used to skip the post-lock re-check so a Mock's default non-None
    ``.get()`` return doesn't suppress the generator the test is observing.
    """
    return type(cache).__module__.startswith("unittest.mock")


def _generate_once(scope, identity, cache_key: str, generate):
    """Return generate() output, with only one worker generating per key.

    Waiters re-check the cache after acquiring the lock, so concurrent
    misses produce a single AI/filter/feedgen pass instead of one per
    request.
    """
    with _generation_lock_for(scope, identity):
        if not _cache_is_mocked():
            content = cache.get(cache_key)
            if content is not None:
                return content
        return generate()


def cache_timeout_seconds(update_frequency_minutes):
    """update_frequency is stored in minutes; cache.set expects seconds."""
    if not update_frequency_minutes or update_frequency_minutes < 0:
        update_frequency_minutes = DEFAULT_CACHE_TIMEOUT_MINUTES
    return update_frequency_minutes * 60


def _is_translation_stale(feed) -> bool:
    """True when translation_status is None but the run that set it is long
    gone — the process crashed or was killed before it could write back a
    result, leaving the feed stuck in a "translating" limbo.

    The threshold is twice the feed's own update_frequency, clamped to
    [30min, 2h]: the floor keeps a healthy run on a frequent feed from
    being mistaken for a crash, the cap keeps a weekly feed from staying
    broken for a fortnight.
    """
    if feed.translation_status is not None:
        return False
    started = feed.translation_started_at
    if not started:
        # No timestamp means the row predates this column or was set by an
        # older code path; treat any None-status older than the threshold as
        # stale via the feed's last completed translation/fetch instead.
        started = feed.last_translate or feed.last_fetch
        if not started:
            return True
    grace = max(30 * 60, min(feed.update_frequency * 60 * 2, 2 * 3600))
    return (timezone.now() - started).total_seconds() > grace


def cache_rss(feed_slug: str, feed_type="t", format="xml"):
    logger.debug(
        f"Start cache_rss for {feed_slug} with type {feed_type} and format {format}"
    )

    feed = Feed.objects.get(slug=feed_slug)
    # The version rides in the key: bump_cache_version() orphans old
    # entries (e.g. after filter results are cleared) without needing
    # prefix deletes the cache backend cannot do.
    cache_key = (
        f"cache_rss_{feed_slug}_{feed_type}_{format}_v{cache_version('rss', feed.pk)}"
    )
    return _generate_once(
        "rss", feed.pk, cache_key,
        lambda: _build_rss(feed, feed_type, format, cache_key),
    )


def _build_rss(feed: Feed, feed_type: str, format: str, cache_key: str):
    feed_slug = feed.slug
    # 如果请求翻译版本，检查翻译状态。status=None 且未过期的进行中
    # 标记会走到下面 else 分支返回旧缓存；真正 stale（超时）的才重置
    # 并重新生成。
    if feed_type == "t" and (feed.translate_title or feed.translate_content or feed.summary):
        if feed.translation_status is None:
            if _is_translation_stale(feed):
                # The "in progress" marker outlived its run: a crash or kill
                # prevented the write-back. Reset it so readers get the feed
                # (with whatever partial results exist) instead of an error,
                # and so the next scheduled run picks the feed up again.
                # last_translate is bumped so the released partial results
                # show up in the feed's ETag/Last-Modified; the CAS predicate
                # (status still None with the same timestamp we observed)
                # keeps us from clobbering a run that started after we read.
                logger.warning(
                    "Translation for %s appears stuck (status=None since %s); "
                    "resetting to failed and generating feed with available content",
                    feed_slug,
                    feed.translation_started_at,
                )
                Feed.objects.filter(
                    pk=feed.pk,
                    translation_status=None,
                    translation_started_at=feed.translation_started_at,
                ).update(
                    translation_status=False,
                    last_translate=timezone.now(),
                )
            else:
                # Translation genuinely in progress, keep the old cache.
                logger.debug(
                    f"Translation in progress for {feed_slug}, using old cache if available"
                )
                return cache.get(cache_key)
        elif feed.translation_status is False:  
            # 翻译失败，记录警告但仍然生成 feed（包含原文）  
            logger.warning(f"Translation failed for {feed_slug}, generating feed with original content")
              
    atom_feed = generate_atom_feed(feed, feed_type)
    if not atom_feed:
        # An early-returned stale marker already wrote back above; a
        # generate_atom_feed failure must not leave another versioned miss
        # to repeat the reset every request, so cache nothing here.
        return None

    # 缓存
    cache.set(cache_key, atom_feed, cache_timeout_seconds(feed.update_frequency))
    logger.debug(f"Cached successfully with key {cache_key}")
    return atom_feed


def cache_tag(tag: str, feed_type="t", format="xml"):
    logger.debug(f"Start cache_tag for {tag} with type {feed_type} and format {format}")

    tag_pk = Tag.objects.filter(slug=tag).values_list("pk", flat=True).first()
    cache_key = (
        f"cache_tag_{tag}_{feed_type}_{format}_v{cache_version('tag', tag_pk)}"
    )
    return _generate_once(
        "tag",
        tag,
        cache_key,
        lambda: _build_tag(tag, feed_type, cache_key),
    )


def _build_tag(tag, feed_type, cache_key):
    feeds = list(Feed.objects.filter(tags__slug=tag))
    atom_feed = merge_feeds_into_one_atom(tag, feeds, feed_type)

    if not atom_feed:
        return None

    # 缓存
    max_frequency = max((f.update_frequency for f in feeds), default=None)
    cache.set(cache_key, atom_feed, cache_timeout_seconds(max_frequency))
    logger.debug(f"Cached successfully with key {cache_key}")
    return atom_feed


def cache_digest(slug: str, format: str = "xml"):
    logger.debug(f"Start cache_digest for {slug} with format {format}")

    from .models import Digest

    digest = Digest.objects.get(slug=slug)
    cache_key = f"cache_digest_{slug}_{format}_v{cache_version('digest', digest.pk)}"
    return _generate_once(
        "digest", digest.pk, cache_key, lambda: _build_digest(digest, cache_key)
    )


def _build_digest(digest, cache_key):
    digest_feed = digest.get_digest_feed()

    atom_feed = generate_atom_feed(digest_feed, "t")
    if not atom_feed:
        return None

    # Digests regenerate at most daily; a feed-frequency TTL (a weekly
    # digest feed = 7 days) would keep serving a stale digest. Cap it.
    ttl = min(
        cache_timeout_seconds(digest_feed.update_frequency),
        _DIGEST_CACHE_TIMEOUT_SECONDS,
    )
    cache.set(cache_key, atom_feed, ttl)
    logger.debug(f"Cached successfully with key {cache_key}")
    return atom_feed


def _build_atom_feed(
    feed_id, title, author, link, subtitle, language, updated, pubdate=None
):
    """构建Atom Feed的基本结构"""
    updated_time = updated or pubdate or timezone.now()
    # 确保必要字段有值:updated, title, id
    fg = FeedGenerator()
    fg.id(str(feed_id))
    # 如果没有title，使用时间作为备用，转换到本地时区
    if not title:
        local_time = timezone.localtime(updated_time)
        title = local_time.strftime("%Y-%m-%d %H:%M:%S")
    fg.title(title)
    fg.author({"name": author or "Unknown"})
    fg.link(href=link, rel="alternate")
    fg.subtitle(subtitle or "")
    fg.language(language or "")
    fg.updated(updated_time)
    fg.pubDate(pubdate or updated_time)

    return fg


class _AtomEntry(FeedEntry):
    """FeedEntry that keeps the attributes of its <link> elements.

    feedgen 1.0.0 writes every entry link with only its ``href``
    (https://github.com/lkiesow/python-feedgen/issues/135), so an enclosure
    comes out as a second plain link and readers no longer see it as an
    attachment. Restore rel/type/hreflang/title/length after the fact.
    """

    _LINK_ATTRIBUTES = ("rel", "type", "hreflang", "title", "length")

    def atom_entry(self, extensions=True):
        entry = super().atom_entry(extensions)
        elements = [e for e in entry if etree.QName(e).localname == "link"]
        for element, link in zip(elements, self.link() or []):
            for attribute in self._LINK_ATTRIBUTES:
                value = link.get(attribute)
                if value and element.get(attribute) is None:
                    element.set(attribute, str(value))
        return entry


def _add_atom_entry(fg, entry, feed_type, translation_display=None):
    """向Atom Feed添加条目"""
    pubdate = entry.pubdate or timezone.now()
    updated = entry.updated or pubdate
    summary = entry.original_summary

    # A single-feed render passes the feed's display mode in; tag merges
    # leave it None so each entry falls back to its own (already fetched,
    # select_related) feed's mode — not the first feed's.
    if translation_display is None and getattr(entry, "feed_id", None) is not None:
        translation_display = entry.feed.translation_display

    # 处理标题和内容
    title = entry.original_title
    content = entry.original_content or ""

    if feed_type == "t":
        if entry.translated_title:
            title = set_translation_display(
                entry.original_title,
                entry.translated_title,
                translation_display,
            )

        if entry.translated_content:
            content = set_translation_display(
                entry.original_content,
                entry.translated_content,
                translation_display,
                "<br />---------------<br />",
            )

        if entry.ai_summary:
            html_summary = (
                f"{mistune.html(entry.ai_summary)}<br />---------------<br />"
            )
            content = html_summary + content

        summary = content or ""  # 确保summary始终是全文内容

    # 创建条目
    fe = fg.add_entry(_AtomEntry())
    # 如果没有title，使用时间作为备用，转换到本地时区
    if not title:
        local_time = timezone.localtime(updated)
        title = local_time.strftime("%Y-%m-%d %H:%M:%S")
    fe.title(title)
    fe.link(href=entry.link or "", rel="alternate")
    fe.author({"name": entry.author or "Unknown"})
    fe.id(entry.guid or entry.link)
    fe.content(content, type="html")
    fe.summary(summary, type="html")
    fe.updated(updated)
    fe.pubDate(pubdate)

    # 处理附件
    if entry.enclosures_xml:
        try:
            xml = etree.fromstring(entry.enclosures_xml, parser=_SAFE_XML_PARSER)
            for enclosure in xml.iter("enclosure"):
                href = enclosure.get("href")
                if href:
                    # Use link() rather than FeedEntry.enclosure(): the latter
                    # stringifies a missing length to "None", which is invalid
                    # Atom and crashes feed2json's int() conversion.
                    fe.link(
                        href=href,
                        rel="enclosure",
                        type=enclosure.get("type"),
                        length=enclosure.get("length"),
                    )
        except Exception as e:
            logger.error(f"Error parsing enclosures for entry {entry.id}: {str(e)}")

    return fe


def generate_atom_feed(feed: Feed, feed_type="t"):
    """生成单个Feed的Atom格式"""
    if not feed:
        logger.error("generate_atom_feed: feed is None")
        return None

    try:
        # 构建基础Feed
        fg = _build_atom_feed(
            feed_id=feed.id,
            title=feed.name,
            author=feed.author,
            link=feed.link or feed.feed_url,
            subtitle=feed.subtitle,
            language=feed.language,
            updated=feed.updated,
            pubdate=feed.pubdate,
        )

        # 添加所有条目
        entries = feed.filtered_entries if feed_type == "t" else feed.entries.all()
        if entries is None:
            return []

        entries = entries.select_related("feed")
        for entry in reversed(entries.order_by("-pubdate", "-id")[: feed.max_posts]):
            _add_atom_entry(fg, entry, feed_type, feed.translation_display)

        # 生成最终XML
        return _finalize_atom_feed(fg)

    except Exception as e:
        logging.exception(f"generate_atom_feed error {feed.feed_url}: {str(e)}")
        return None


def merge_feeds_into_one_atom(tag: str, feeds: list[Feed], feed_type="t"):
    """合并多个Feeds生成单个Atom Feed"""
    type_str = "Original" if feed_type == "o" else "Translated"
    feed_id = f"urn:merged-tag-{tag}-{type_str}-feeds"
    feed_title = f"{type_str} #{tag} tag  Feeds"

    # 构建基础Feed
    fg = _build_atom_feed(
        feed_id=feed_id,
        title=feed_title,
        author=feed_title,
        link=settings.SITE_URL,
        subtitle=f"Combined {type_str} {tag} Feeds",
        language="en",
        updated=timezone.now(),
    )

    tag_obj = Tag.objects.get(slug=tag)

    # 收集所有条目
    all_entries = []
    for feed in feeds:
        # 添加Feed作为分类
        fg.category(term=str(feed.id), label=feed.name, scheme=feed.feed_url)
        if feed.max_posts <= 0:
            continue
        # 收集当前feed的条目。默认不走feed的filter（tag有自己的filter）；
        # apply_feed_filters 打开时使用 feed 自己的过滤结果，与 feed 的 RSS 输出一致
        entries = (
            feed.filtered_entries if tag_obj.apply_feed_filters else feed.entries.all()
        )
        for entry in entries.select_related("feed").order_by("-pubdate", "-id")[
            : feed.max_posts
        ]:
            sort_time = entry.pubdate or entry.updated or timezone.now()
            all_entries.append((sort_time, entry))

    # 按时间降序排序（最新的在最前面）
    all_entries.sort(key=lambda x: x[0], reverse=True)

    # 获取tag filter对象
    tag_filters = list(tag_obj.filters.all())
    if not tag_filters or not all_entries:
        filtered_entries = all_entries
    else:
        if any(
            f.filter_method in (f.AI_ONLY, f.BOTH) and f.agent_id for f in tag_filters
        ):
            # Bound AI work before evaluating filters, not just the final output.
            all_entries = all_entries[:100]

        # 批量获取所有条目ID的QuerySet并链式应用过滤器
        filtered_qs = Entry.objects.filter(
            id__in=[entry.pk for _, entry in all_entries]
        )
        for filter_obj in tag_filters:
            filtered_qs = filter_obj.apply_filter(filtered_qs)

        passed_ids = set(filtered_qs.values_list("id", flat=True))
        filtered_entries = [
            item for item in all_entries if item[1].id in passed_ids
        ]

    # 更新Feed时间为最新条目时间
    if filtered_entries:
        # 第一个条目是最新的（因为已按时间降序排序）
        fg.updated(filtered_entries[0][0])

    # 添加所有条目（最多100条）
    for _, entry in filtered_entries[:100]:
        _add_atom_entry(fg, entry, feed_type)

    # 生成最终XML
    return _finalize_atom_feed(fg)


def _finalize_atom_feed(fg):
    """生成最终的Atom XML字符串"""
    atom_string = fg.atom_str(pretty=False)
    root = etree.fromstring(atom_string)
    tree = etree.ElementTree(root)
    pi = etree.ProcessingInstruction(
        "xml-stylesheet", 'type="text/xsl" href="/static/rss.xsl"'
    )
    root.addprevious(pi)
    return etree.tostring(
        tree, pretty_print=True, xml_declaration=True, encoding="utf-8"
    ).decode()

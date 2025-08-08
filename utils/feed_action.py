import logging
import time

from django.utils import timezone

from django.conf import settings

from typing import Dict

import feedparser
from lxml import etree
import mistune
from feedgen.feed import FeedGenerator
from core.models import Feed, Entry, Tag
from utils.text_handler import set_translation_display
from fake_useragent import UserAgent

def convert_struct_time_to_datetime(time_str):
    if not time_str:
        return None
    return timezone.datetime.fromtimestamp(
        time.mktime(time_str), tz=timezone.get_default_timezone()
    )


def manual_fetch_feed(url: str, etag: str = "") -> Dict:
    import httpx

    update = False
    feed = {}
    error = None
    response = None
    ua = UserAgent()
    headers = {
        "If-None-Match": etag,
        #'If-Modified-Since': modified,
        "User-Agent": ua.random.strip(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    }

    client = httpx.Client()

    try:
        response = client.get(url, headers=headers, timeout=30, follow_redirects=True)

        if response.status_code == 200:
            feed = feedparser.parse(response.text)
            update = True
        elif response.status_code == 304:
            update = False
        else:
            response.raise_for_status()

    except httpx.HTTPStatusError as exc:
        error = f"HTTP status error while requesting {url}: {exc.response.status_code} {exc.response.reason_phrase}"
    except httpx.TimeoutException:
        error = f"Timeout while requesting {url}"
    except Exception as e:
        error = f"Error while requesting {url}: {str(e)}"

    if feed:
        if feed.bozo and not feed.entries:
            logging.warning("Get feed %s %s", url, feed.get("bozo_exception"))
            error = feed.get("bozo_exception")

    return {
        "feed": feed,
        "update": update,
        "error": error,
    }


def fetch_feed(url: str, etag: str = "") -> Dict:
    try:
        ua = UserAgent()
        feed = feedparser.parse(url, etag=etag, agent=ua.random.strip())
        if feed.status == 304:
            logging.info(f"Feed {url} not modified, using cached version.")
            return {
                "feed": None,
                "update": False,
                "error": None,
            }
        if feed.bozo and not feed.entries:
            logging.warning("Manual fetch feed %s %s", url, feed.get("bozo_exception"))
            results = manual_fetch_feed(url, etag)
            return results
        else:
            return {
                "feed": feed,
                "update": True,
                "error": None,
            }
    except Exception as e:
        # logging.warning(f"Failed to fetch feed {url}: {str(e)}")
        return {
            "feed": None,
            "update": False,
            "error": str(e),
        }


def _build_atom_feed(
    feed_id, title, author, link, subtitle, language, updated, pubdate=None
):
    """构建Atom Feed的基本结构"""
    updated_time = updated or pubdate or timezone.now()
    # 确保必要字段有值:updated, title, id
    fg = FeedGenerator()
    fg.id(str(feed_id))
    fg.title(title or updated_time.strftime("%Y-%m-%d %H:%M:%S"))
    fg.author({"name": author or "Unknown"})
    fg.link(href=link, rel="alternate")
    fg.subtitle(subtitle or "")
    fg.language(language or "")
    fg.updated(updated_time)
    fg.pubDate(pubdate or updated_time)

    return fg


def _add_atom_entry(fg, entry, feed_type, translation_display=None):
    """向Atom Feed添加条目"""
    pubdate = entry.pubdate or timezone.now()
    updated = entry.updated or pubdate
    summary = entry.original_summary

    # 处理标题和内容
    title = entry.original_title
    content = entry.original_content

    if feed_type == "t":
        if entry.translated_title:
            title = set_translation_display(
                entry.original_title,
                entry.translated_title,
                translation_display or entry.feed.translation_display,
            )

        if entry.translated_content:
            content = set_translation_display(
                entry.original_content,
                entry.translated_content,
                translation_display or entry.feed.translation_display,
                "<br />---------------<br />",
            )
            summary = content[:100] + "..." if len(content) > 100 else content

        if entry.ai_summary:
            html_summary = (
                f"<br />🤖:{mistune.html(entry.ai_summary)}<br />---------------<br />"
            )
            content = html_summary + content
            summary = entry.ai_summary

    # 创建条目
    fe = fg.add_entry()
    fe.title(title or updated.strftime("%Y-%m-%d %H:%M:%S"))
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
            xml = etree.fromstring(entry.enclosures_xml)
            for enclosure in xml.iter("enclosure"):
                fe.enclosure(
                    url=enclosure.get("href"),
                    type=enclosure.get("type"),
                    length=enclosure.get("length"),
                )
        except Exception as e:
            logging.error(f"Error parsing enclosures for entry {entry.id}: {str(e)}")

    return fe


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


def generate_atom_feed(feed: Feed, feed_type="t"):
    """生成单个Feed的Atom格式"""
    if not feed:
        logging.error("generate_atom_feed: feed is None")
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
        
        for entry in reversed(entries.order_by("-pubdate")[: feed.max_posts]):
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

    # 收集所有条目
    all_entries = []
    entry_ids = []  # 用于存储所有条目的ID
    for feed in feeds:
        # 添加Feed作为分类
        fg.category(term=str(feed.id), label=feed.name, scheme=feed.feed_url)
        # 收集当前feed的条目
        entries = feed.entries.all()  # tag的条目不走feed的filter，因为tag有自己的filter
        if not entries:
            continue

        for entry in reversed(entries.order_by("-pubdate")[: feed.max_posts]):
            sort_time = entry.pubdate or entry.updated or timezone.now()
            all_entries.append((sort_time, entry))
            entry_ids.append(entry.id)

    # 按时间降序排序（最新的在最前面）
    all_entries.sort(key=lambda x: x[0], reverse=True)

    # 获取tag filter对象
    tag_filters = Tag.objects.get(slug=tag).filters.all()
    
    # 开始过滤 - 使用批量查询优化性能
    if not tag_filters:
        # 没有过滤器，直接使用所有条目
        filtered_entries = [entry for (_, entry) in all_entries]
    else:
        # 批量获取所有条目ID的QuerySet
        base_qs = Entry.objects.filter(id__in=entry_ids)
        
        # 应用所有过滤器（链式应用）
        filtered_qs = base_qs
        for filter_obj in tag_filters:
            filtered_qs = filter_obj.apply_filter(filtered_qs)
        
        # 获取通过过滤的条目ID集合
        passed_ids = set(filtered_qs.values_list('id', flat=True))
        
        # 构建过滤后的条目列表（保持原排序）
        filtered_entries = [
            entry for (_, entry) in all_entries 
            if entry.id in passed_ids
        ]

    # 更新Feed时间为最新条目时间
    if filtered_entries:
        # 第一个条目是最新的（因为已按时间降序排序）
        latest_time = all_entries[0][0]
        fg.updated(latest_time)

    # 添加所有条目（最多100条）
    for entry in filtered_entries[:100]:
        _add_atom_entry(fg, entry, feed_type)

    # 生成最终XML
    return _finalize_atom_feed(fg)
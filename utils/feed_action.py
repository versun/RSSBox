
import logging
import time

from django.utils import timezone

from django.conf import settings

from typing import Dict

import feedparser
from lxml import etree
import mistune
from feedgen.feed import FeedGenerator
from core.models import Feed, Entry
from utils.text_handler import set_translation_display

def convert_struct_time_to_datetime(time_str):
    if not time_str:
        return None
    return timezone.datetime.fromtimestamp(time.mktime(time_str), tz=timezone.get_default_timezone())

def fetch_feed(url: str, etag: str = "") -> Dict:
    try:
        feed = feedparser.parse(url)
        if feed.bozo and not feed.entries:
            return {
                "feed": feed,
                "update": False,
                "error": feed.get("bozo_exception"),
            }
        else:
            return {
                "feed": feed,
                "update": True,
                "error": None,
            }
    except Exception as e:
        return {
            "feed": None,
            "update": False,
            "error": str(e),
        }

def _build_atom_feed(feed_id, title, author, link, subtitle, language, updated, pubdate=None):
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
            title = set_translation_display(entry.original_title, entry.translated_title, 
                                      translation_display or entry.feed.translation_display)
        
        if entry.translated_content:
            content = set_translation_display(entry.original_content, entry.translated_content, 
                                        translation_display or entry.feed.translation_display)
        
        if entry.ai_summary:
            html_summary = f"<br />🤖:{mistune.html(entry.ai_summary)}<br />---------------<br />"
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
        "xml-stylesheet", 
        'type="text/xsl" href="/static/rss.xsl"'
    )
    root.addprevious(pi)
    return etree.tostring(
        tree,
        pretty_print=True,
        xml_declaration=True,
        encoding="utf-8"
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
            pubdate=feed.pubdate
        )
        
        # 添加所有条目
        for entry in feed.entries.all():
            _add_atom_entry(fg, entry, feed_type, feed.translation_display)
        
        # 生成最终XML
        return _finalize_atom_feed(fg)
    
    except Exception as e:

        logging.exception(f"generate_atom_feed error {feed.feed_url}: {str(e)}")
        return None

def merge_feeds_into_one_atom(category: str, feeds: list[Feed], feed_type="t"):
    """合并多个Feeds生成单个Atom Feed"""
    type_str = "Original" if feed_type == "o" else "Translated"
    feed_id = f'urn:merged-category-{category}-{type_str}-feeds'
    feed_title = f'{type_str} Category {category} Feeds'
    
    # 构建基础Feed
    fg = _build_atom_feed(
        feed_id=feed_id,
        title=feed_title,
        author=feed_title,
        link=settings.SITE_URL,
        subtitle=f'Combined {type_str} {category} Feeds',
        language='en',
        updated=timezone.now()
    )
    
    # 收集所有条目（限制总数为100）
    all_entries = []
    for feed in feeds:
        # 添加Feed作为分类
        fg.category(
            term=str(feed.id),
            label=feed.name,
            scheme=feed.feed_url
        )
        # 收集当前feed的条目（已按时间顺序）
        for entry in feed.entries.all():
            # 如果已经达到100条，则跳出循环
            if len(all_entries) >= 100:
                break
            sort_time = entry.pubdate or entry.updated or timezone.now()
            all_entries.append((sort_time, entry))
    
    # 更新Feed时间为最新条目时间
    if all_entries:
        # 由于条目已按时间顺序添加，第一个就是最新的
        latest_time = all_entries[0][0]
        fg.updated(latest_time)
    
    # 添加所有条目
    for _, entry in all_entries:
        _add_atom_entry(fg, entry, feed_type)
    
    # 生成最终XML
    return _finalize_atom_feed(fg)
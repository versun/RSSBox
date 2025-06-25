
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
    fg.author({"name": author})
    fg.link(href=link, rel="alternate")
    fg.subtitle(subtitle or "")
    fg.language(language or "")
    fg.updated(updated_time)
    fg.pubDate(pubdate or updated_time)
    
    return fg

def _add_atom_entry(fg, entry, type, translation_display=None):
    """向Atom Feed添加条目"""
    pubdate = entry.pubdate or timezone.now()
    updated = entry.updated or pubdate
    summary = entry.original_summary

    # 处理标题和内容
    if type == "o":
        title = entry.original_title
        content = entry.original_content
    else:
        title = set_translation_display(entry.original_title, entry.translated_title, 
                                      translation_display or entry.feed.translation_display)
        content = set_translation_display(entry.original_content, entry.translated_content, 
                                        translation_display or entry.feed.translation_display)
        
        # 添加AI摘要
        if entry.ai_summary:
            html_summary = f"<br />🤖:{mistune.html(entry.ai_summary)}<br />---------------<br />"
            content = html_summary + content
            summary = entry.ai_summary

    # 创建条目
    fe = fg.add_entry()
    fe.title(title or updated.strftime("%Y-%m-%d %H:%M:%S"))
    fe.link(href=entry.link)
    fe.author({"name": entry.author})
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

def generate_atom_feed(feed: Feed, type="t"):
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
            link=feed.link,
            subtitle=feed.subtitle,
            language=feed.language,
            updated=feed.updated,
            pubdate=feed.pubdate
        )
        
        # 添加所有条目
        for entry in feed.entries.all():
            _add_atom_entry(fg, entry, type, feed.translation_display)
        
        # 生成最终XML
        return _finalize_atom_feed(fg)
    
    except Exception as e:
        logging.error(f"generate_atom_feed error {feed.feed_url}: {str(e)}")
        return None

def merge_feeds_into_one_atom(category: str, feeds: list[Feed], type="t"):
    """合并多个Feeds生成单个Atom Feed"""
    type_str = "Original" if type == "o" else "Translated"
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
        _add_atom_entry(fg, entry, type)
    
    # 生成最终XML
    return _finalize_atom_feed(fg)

# 请勿使用django的feedgenerator，生成的feed没有内容，只有标题
# def generate_atom_feed(feed: Feed, type="t"):
#     type_str = "Original" if type == "o" else "Translated"

#     if not feed:
#         logging.error("generate_atom_feed: feed is None")
#         return None
#     try:
#         pubdate = feed.pubdate
#         updated = feed.updated

#         title = feed.name
#         subtitle = feed.subtitle
#         link = feed.link
#         language = feed.language
#         author_name = feed.author
#         # logging.info("generate_atom_feed:%s,%s,%s,%s,%s",title,subtitle,link,language,author_name)

#         fg = FeedGenerator()
#         fg.id(str(feed.id))
#         fg.title(title)
#         fg.author({"name": author_name})
#         fg.link(href=link, rel="alternate")
#         fg.subtitle(subtitle)
#         fg.language(language)
#         fg.updated(updated)
#         fg.pubDate(pubdate)

#         if not fg.updated():
#             fg.updated(pubdate if pubdate else timezone.now())
#         if not fg.title():
#             fg.title(updated)
#         if not fg.id():
#             fg.id(fg.title())

#         for entry in feed.entries.all():
#             pubdate = entry.pubdate
#             updated = entry.updated
#             summary = entry.original_summary

#             if type == "o":
#                 title = entry.original_title
#                 content = entry.original_content
#             else:
#                 title = set_translation_display(entry.original_title, entry.translated_title, feed.translation_display)
                
#                 content = set_translation_display(entry.original_content, entry.translated_content, feed.translation_display)

#                 if entry.ai_summary:
#                     summary = entry.ai_summary
#                     html_summary = f"<br />🤖:{mistune.html(summary)}<br />---------------<br />"
#                     content = html_summary + content

#             link = entry.link
#             unique_id = entry.guid

#             author_name = entry.author

#             fe = fg.add_entry(order="append")
#             fe.title(title)
#             fe.link(href=link)
#             fe.author({"name": author_name})
#             fe.id(unique_id)
#             fe.content(content, type="html")
#             fe.updated(updated)
#             fe.pubDate(pubdate)
#             fe.summary(summary, type="html")

#             if entry.enclosures_xml:
#                 xml = etree.fromstring(entry.enclosures_xml)    
#                 for enclosure in xml.iter("enclosure"):
#                     fe.enclosure(
#                         url=enclosure.get("href"),
#                         type=enclosure.get("type"),
#                         length=enclosure.get("length"),
#                     )

#             # id, title, updated are required
#             if not fe.updated():
#                 fe.updated(pubdate if pubdate else timezone.now())
#             if not fe.title():
#                 fe.title(updated.strftime("%Y-%m-%d %H:%M:%S"))
#             if not fe.id():
#                 fe.id(fe.title())

#         # fg.atom_file(file_path, extensions=True, pretty=True, encoding='UTF-8', xml_declaration=True)
#         atom_string = fg.atom_str(pretty=False)

#     except Exception as e:
#         logging.error("generate_atom_feed error %s: %s", feed.feed_url, str(e))
#         return None

#     # dom = xml.dom.minidom.parseString(atom_string)
#     # pi = dom.createProcessingInstruction("xml-stylesheet", 'type="text/xsl" href="/static/rss.xsl"')
#     # dom.insertBefore(pi, dom.firstChild)
#     # atom_string_with_pi = dom.toprettyxml()

#     root = etree.fromstring(atom_string)
#     tree = etree.ElementTree(root)
#     pi = etree.ProcessingInstruction("xml-stylesheet",
#                                      'type="text/xsl" href="/static/rss.xsl"')
#     root.addprevious(pi)
#     atom_string_with_pi = etree.tostring(tree,
#                                          pretty_print=True,
#                                          xml_declaration=True,
#                                          encoding="utf-8").decode()

#     return atom_string_with_pi


# def merge_feeds_into_one_atom(category: str, feeds: list[Feed], type="t"):
#     # 创建合并后的Feed生成器
#     type_str = "Original" if type == "o" else "Translated"
#     fg = FeedGenerator()
#     fg.id(f'urn:merged-category-{category}-{type_str}-feeds')
#     fg.title(f'{type_str} Category {category} Feeds')
#     fg.author({'name': f'{type_str} Category {category} Feeds'})
#     fg.link(href=settings.SITE_URL, rel='alternate')  # 使用项目中的SITE_URL设置
#     fg.subtitle(f'Combined {type_str} {category} Feeds')
#     fg.language('en')
    
#     # 收集所有条目并确定最新更新时间
#     all_entries = []
#     latest_updated = None
    
#     for feed in feeds:
#         # 添加Feed信息作为分类
#         fg.category(
#             term=str(feed.id),
#             label=feed.name,
#             scheme=feed.feed_url
#         )
        
#         # 处理每个条目
#         for entry in feed.entries.all():
#             # 确定排序时间（优先使用发布时间）
#             sort_time = entry.pubdate if entry.pubdate else entry.updated
            
#             # 更新最新更新时间
#             if not latest_updated or (sort_time and sort_time > latest_updated):
#                 latest_updated = sort_time
                
#             all_entries.append((sort_time, entry))
    
#     # 按时间降序排序（最新的在前）
#     all_entries.sort(key=lambda x: x[0] or timezone.now(), reverse=True)
    
#     # 设置Feed更新时间
#     fg.updated(latest_updated or timezone.now())
    
#     # 添加所有条目到合并的Feed
#     for sort_time, entry in all_entries:
#         title = entry.original_title if type == "o" else entry.translated_title
#         fe = fg.add_entry()
#         fe.id(entry.guid or entry.link)
#         fe.title(title)
#         fe.link(href=entry.link)
        
#         if entry.author:
#             fe.author({'name': entry.author})
        
#         if entry.original_content:
#             fe.content(entry.original_content if type == "o" else entry.translated_content, type='html')
        
#         if entry.original_summary:
#             fe.summary(entry.original_summary if type == "o" else entry.ai_summary, type='html')
        
#         # 处理时间信息
#         if entry.pubdate:
#             fe.pubDate(entry.pubdate)
#         if entry.updated:
#             fe.updated(entry.updated)
        
#         if not fe.updated():
#                 fe.updated(entry.pubdate or timezone.now())
#         if not fe.title():
#             fe.title(entry.updated.strftime("%Y-%m-%d %H:%M:%S"))
#         if not fe.id():
#             fe.id(fe.title())
        
#         # 处理附件
#         if entry.enclosures_xml:
#             try:
#                 xml = etree.fromstring(entry.enclosures_xml)
#                 for enclosure in xml.iter("enclosure"):
#                     fe.enclosure(
#                         url=enclosure.get("href"),
#                         type=enclosure.get("type"),
#                         length=enclosure.get("length"),
#                     )
#             except Exception as e:
#                 logging.error(f"Error parsing enclosures for entry {entry.id}: {str(e)}")
    
#     # 生成Atom XML并添加样式表
#     atom_string = fg.atom_str(pretty=False)
#     root = etree.fromstring(atom_string)
#     tree = etree.ElementTree(root)
#     pi = etree.ProcessingInstruction(
#         "xml-stylesheet", 
#         'type="text/xsl" href="/static/rss.xsl"'
#     )
#     root.addprevious(pi)
    
#     return etree.tostring(
#         tree,
#         pretty_print=True,
#         xml_declaration=True,
#         encoding="utf-8"
#     ).decode()
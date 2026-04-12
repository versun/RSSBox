import logging

import mistune
from django.conf import settings
from django.utils import timezone
from feedgen.feed import FeedGenerator
from lxml import etree

from utils.text_handler import set_translation_display

logger = logging.getLogger(__name__)


def build_atom_feed(
    feed_id,
    title,
    author,
    link,
    subtitle,
    language,
    updated,
    pubdate=None,
):
    updated_time = updated or pubdate or timezone.now()
    fg = FeedGenerator()
    fg.id(str(feed_id))
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


def add_atom_entry(fg, entry, feed_type, translation_display=None, entry_logger=None):
    entry_logger = entry_logger or logger
    pubdate = entry.pubdate or timezone.now()
    updated = entry.updated or pubdate
    summary = entry.original_summary
    title = entry.original_title
    content = entry.original_content or ""

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

        if entry.ai_summary:
            html_summary = f"{mistune.html(entry.ai_summary)}<br />---------------<br />"
            content = html_summary + content

        summary = content or ""

    fe = fg.add_entry()
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

    if entry.enclosures_xml:
        try:
            xml = etree.fromstring(entry.enclosures_xml)
            for enclosure in xml.iter("enclosure"):
                fe.enclosure(
                    url=enclosure.get("href"),
                    type=enclosure.get("type"),
                    length=enclosure.get("length"),
                )
        except Exception as exc:
            entry_logger.error(f"Error parsing enclosures for entry {entry.id}: {str(exc)}")

    return fe


def finalize_atom_feed(fg):
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


def render_feed_content(
    feed,
    feed_type="t",
    *,
    build_feed_func=build_atom_feed,
    add_entry_func=add_atom_entry,
    finalize_func=finalize_atom_feed,
    render_logger=None,
):
    render_logger = render_logger or logger
    if not feed:
        render_logger.error("generate_atom_feed: feed is None")
        return None

    try:
        fg = build_feed_func(
            feed_id=feed.id,
            title=feed.name,
            author=feed.author,
            link=feed.link or feed.feed_url,
            subtitle=feed.subtitle,
            language=feed.language,
            updated=feed.updated,
            pubdate=feed.pubdate,
        )

        entries = feed.filtered_entries if feed_type == "t" else feed.entries.all()
        if entries is None:
            return []

        for entry in reversed(entries.order_by("-pubdate")[: feed.max_posts]):
            add_entry_func(
                fg,
                entry,
                feed_type,
                feed.translation_display,
                entry_logger=render_logger,
            )

        return finalize_func(fg)
    except Exception as exc:
        render_logger.exception(f"generate_atom_feed error {feed.feed_url}: {str(exc)}")
        return None


def render_tag_content(
    tag,
    feeds,
    feed_type="t",
    *,
    build_feed_func=build_atom_feed,
    add_entry_func=add_atom_entry,
    finalize_func=finalize_atom_feed,
):
    from core.services.feed.filters import apply_tag_filters

    type_str = "Original" if feed_type == "o" else "Translated"
    fg = build_feed_func(
        feed_id=f"urn:merged-tag-{tag}-{type_str}-feeds",
        title=f"{type_str} #{tag} tag  Feeds",
        author=f"{type_str} #{tag} tag  Feeds",
        link=settings.SITE_URL,
        subtitle=f"Combined {type_str} {tag} Feeds",
        language="en",
        updated=timezone.now(),
    )

    all_entries = []
    entry_ids = []
    for feed in feeds:
        fg.category(term=str(feed.id), label=feed.name, scheme=feed.feed_url)
        entries = feed.entries.all()
        if not entries:
            continue

        for entry in reversed(entries.order_by("-pubdate")[: feed.max_posts]):
            sort_time = entry.pubdate or entry.updated or timezone.now()
            all_entries.append((sort_time, entry))
            entry_ids.append(entry.id)

    all_entries.sort(key=lambda item: item[0], reverse=True)
    filtered_entries = apply_tag_filters(tag, entry_ids, all_entries)

    if filtered_entries:
        fg.updated(all_entries[0][0])

    for entry in filtered_entries[:100]:
        add_entry_func(fg, entry, feed_type)

    return finalize_func(fg)

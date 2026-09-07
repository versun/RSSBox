from datetime import datetime

from django.http import HttpResponse
from lxml import etree

from core.models import Feed, Tag


def import_opml_content(opml_content: bytes) -> int:
    parser = etree.XMLParser(resolve_entities=False)
    root = etree.fromstring(opml_content, parser=parser)
    body = root.find("body")

    if body is None:
        raise ValueError("Invalid OPML: Missing body element")

    created_count = 0

    def process_outlines(outlines, tag_name: str = None):
        nonlocal created_count
        for outline in outlines:
            if "xmlUrl" in outline.attrib:
                feed, created = Feed.objects.get_or_create(
                    feed_url=outline.get("xmlUrl"),
                    defaults={
                        "name": outline.get("title") or outline.get("text")
                    },
                )
                if created:
                    created_count += 1
                if tag_name:
                    tag_obj, _ = Tag.objects.get_or_create(name=tag_name)
                    feed.tags.add(tag_obj)
            elif outline.find("outline") is not None:
                next_tag_name = outline.get("text") or outline.get("title")
                process_outlines(outline.findall("outline"), next_tag_name)

    process_outlines(body.findall("outline"))
    return created_count


def build_opml_response(title_prefix, queryset, get_feed_url_func, filename_prefix):
    root = etree.Element("opml", version="2.0")

    head = etree.SubElement(root, "head")
    etree.SubElement(head, "title").text = f"{title_prefix} | RSSBox"
    etree.SubElement(head, "dateCreated").text = datetime.now().strftime(
        "%a, %d %b %Y %H:%M:%S %z"
    )
    etree.SubElement(head, "ownerName").text = "RSSBox"

    body = etree.SubElement(root, "body")
    categories = {}
    for feed in queryset:
        feed_tags = list(feed.tags.all()) or [None]
        for tag in feed_tags:
            tag_name = tag.name if tag else "uncategorized"
            if tag_name not in categories:
                categories[tag_name] = etree.SubElement(
                    body, "outline", text=tag_name, title=tag_name
                )

            feed_url = get_feed_url_func(feed) or ""
            feed_name = feed.name or "Untitled Feed"
            etree.SubElement(
                categories[tag_name],
                "outline",
                {
                    "title": feed_name,
                    "text": feed_name,
                    "type": "rss",
                    "xmlUrl": feed_url,
                    "htmlUrl": feed_url,
                },
            )

    xml_content = etree.tostring(
        root, encoding="utf-8", xml_declaration=True, pretty_print=True
    )
    response = HttpResponse(xml_content, content_type="application/xml")
    response["Content-Disposition"] = (
        f'attachment; filename="{filename_prefix}_feeds_from_rssbox.opml"'
    )
    return response

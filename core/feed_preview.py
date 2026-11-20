"""Server-side Atom XML to sanitized HTML preview renderer.

Replaces client-side XSLT (``rss.xsl``), which Chromium is removing
(https://chromestatus.com/feature/4709671889534976). Parses the already-cached
Atom feed XML, sanitizes embedded entry HTML with ``lxml-html-clean``, and
renders a script-free HTML preview page.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import unquote, urlsplit

from django.conf import settings
from django.core.exceptions import DisallowedHost
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.html import escape
from django.utils.safestring import SafeString, mark_safe
from lxml import etree
from lxml import html as lxml_html
from lxml_html_clean import Cleaner

logger = logging.getLogger(__name__)

ATOM_NS = "http://www.w3.org/2005/Atom"
_ATOM_PREFIX = f"{{{ATOM_NS}}}"

_SAFE_XML_PARSER = etree.XMLParser(resolve_entities=False, no_network=True)

_SAFE_LINK_SCHEMES = frozenset({"http", "https", "mailto"})
_SAFE_MEDIA_SCHEMES = frozenset({"http", "https"})
_DEFAULT_SITE_URL = "http://localhost:8000"
_SAFE_DATA_IMAGE_RE = re.compile(
    r"^data:image/(?:png|jpe?g|gif|webp|avif);base64,[A-Za-z0-9+/=\s]+$",
    re.IGNORECASE,
)
# XML 1.0 forbids C0 control chars other than \t, \n, \r as well as UTF-16
# surrogates and U+FFFE/U+FFFF. lxml raises ValueError if any appear in a text
# node (including after libxml2 decodes HTML numeric entities like &#12;).
_INVALID_XML_CHARS_RE = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]"
)
_NUMERIC_CHAR_REF_RE = re.compile(r"&#(?:(\d+)|[xX]([0-9a-fA-F]+));?")

# Strict CSP for preview pages: zero JavaScript execution, no object/form/base
# injection, and opaque null-origin sandboxing isolating the preview from
# Django Admin cookies and storage. Inline <style> is used exclusively by our
# own template; entry inline styles, classes, IDs, and <style> elements are
# stripped during HTML sanitization.
PREVIEW_CSP = (
    "default-src 'none'; "
    "style-src 'unsafe-inline'; "
    "img-src http: https: data:; "
    "media-src http: https:; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "sandbox allow-popups allow-popups-to-escape-sandbox allow-downloads"
)

_HTML_CLEANER = Cleaner(
    scripts=True,
    javascript=True,
    comments=True,
    style=True,
    inline_style=True,
    links=True,
    meta=True,
    page_structure=True,
    processing_instructions=True,
    embedded=True,
    frames=True,
    forms=True,
    annoying_tags=True,
    remove_unknown_tags=False,
    safe_attrs_only=True,
    safe_attrs=(
        (lxml_html.defs.safe_attrs | {"controls", "poster", "loading"})
        - {"class", "id"}
    ),
    remove_tags={"noscript", "xmp", "listing"},
    kill_tags={
        "script",
        "style",
        "iframe",
        "object",
        "embed",
        "applet",
        "form",
        "input",
        "button",
        "textarea",
        "select",
        "option",
        "base",
        "meta",
        "link",
        "svg",
        "math",
        "plaintext",
        "noembed",
        "noframes",
    },
)


@dataclass
class PreviewEnclosure:
    href: str
    label: str
    mime_type: str = ""
    size_display: str = ""


@dataclass
class PreviewEntry:
    title: str
    link: str = ""
    timestamp: str = ""
    raw_timestamp: str = ""
    author: str = ""
    content_html: SafeString = field(default_factory=lambda: mark_safe(""))
    enclosures: list[PreviewEnclosure] = field(default_factory=list)


@dataclass
class PreviewFeed:
    title: str
    subtitle: str = ""
    link: str = ""
    updated: str = ""
    raw_updated: str = ""
    entries: list[PreviewEntry] = field(default_factory=list)


def _is_invalid_xml_codepoint(codepoint: int) -> bool:
    return (
        codepoint <= 0x08
        or codepoint in (0x0B, 0x0C)
        or 0x0E <= codepoint <= 0x1F
        or 0xD800 <= codepoint <= 0xDFFF
        or codepoint in (0xFFFE, 0xFFFF)
        or codepoint > 0x10FFFF
    )


def _strip_invalid_xml_chars(text: str) -> str:
    """Remove raw XML-incompatible chars and HTML numeric entities decoding to them."""

    def _replace_entity(match: re.Match[str]) -> str:
        dec, hex_val = match.groups()
        try:
            codepoint = int(dec, 10) if dec is not None else int(hex_val, 16)
        except ValueError:
            return ""
        return "" if _is_invalid_xml_codepoint(codepoint) else match.group(0)

    cleaned = _INVALID_XML_CHARS_RE.sub("", text)
    while True:
        updated = _NUMERIC_CHAR_REF_RE.sub(_replace_entity, cleaned)
        if updated == cleaned:
            return cleaned
        cleaned = updated


def _safe_url(
    url: str | None,
    *,
    allowed_schemes=_SAFE_LINK_SCHEMES,
) -> str:
    """Return a validated URL, normalizing protocol-relative ``//`` to ``https://``."""
    if not url:
        return ""
    # Strip ASCII control characters and surrounding whitespace before parsing
    # to defeat obfuscations like ``java\tscript:``.
    cleaned = re.sub(r"[\x00-\x1f\x7f]+", "", url).strip()
    if not cleaned:
        return ""
    try:
        parsed = urlsplit(cleaned)
    except ValueError:
        return ""
    if cleaned.startswith("//"):
        if (
            cleaned.startswith("///")
            or parsed.scheme
            or not parsed.hostname
            or "https" not in allowed_schemes
        ):
            return ""
        return f"https:{cleaned}"
    scheme = parsed.scheme.lower()
    if scheme not in allowed_schemes:
        return ""
    if scheme in ("http", "https") and not parsed.hostname:
        return ""
    return cleaned


def _safe_media_url(url: str | None, *, allow_data_image: bool = False) -> str:
    if not url:
        return ""
    cleaned = re.sub(r"[\x00-\x1f\x7f]+", "", url).strip()
    if not cleaned:
        return ""
    if allow_data_image and _SAFE_DATA_IMAGE_RE.match(cleaned):
        return cleaned
    return _safe_url(cleaned, allowed_schemes=_SAFE_MEDIA_SCHEMES)


def _format_timestamp(raw: str | None) -> str:
    if not raw:
        return ""
    value = raw.strip()
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is not None:
            dt = timezone.localtime(dt)
        return dt.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value[:16]


def _format_byte_size(raw: str | None) -> str:
    if not raw or not str(raw).strip().isdigit():
        return ""
    size = float(int(str(raw).strip()))
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}".replace(".0 ", " ")
        size /= 1024
    return ""


def _enclosure_label(href: str, title: str | None, mime_type: str) -> str:
    if title and title.strip():
        return title.strip()
    path = unquote(urlsplit(href).path or "").rstrip("/")
    basename = path.rsplit("/", 1)[-1] if path else ""
    if basename:
        return basename
    if mime_type:
        return f"Attachment ({mime_type})"
    return "Attachment"


def _find_child(parent: etree._Element, local_name: str) -> etree._Element | None:
    child = parent.find(f"{_ATOM_PREFIX}{local_name}")
    if child is None:
        child = parent.find(local_name)
    return child


def _find_children(parent: etree._Element, local_name: str) -> list[etree._Element]:
    children = parent.findall(f"{_ATOM_PREFIX}{local_name}")
    if not children:
        children = parent.findall(local_name)
    return children


def _child_text(parent: etree._Element, local_name: str) -> str:
    child = _find_child(parent, local_name)
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def _extract_alternate_link(parent: etree._Element) -> str:
    fallback = ""
    for link_el in _find_children(parent, "link"):
        href = _safe_url(link_el.get("href"))
        if not href:
            continue
        rel = (link_el.get("rel") or "").strip().lower()
        if rel == "alternate":
            return href
        if not rel and not fallback:
            fallback = href
    return fallback


def _extract_enclosures(entry_el: etree._Element) -> list[PreviewEnclosure]:
    enclosures: list[PreviewEnclosure] = []
    for link_el in _find_children(entry_el, "link"):
        if (link_el.get("rel") or "").strip().lower() != "enclosure":
            continue
        href = _safe_url(link_el.get("href"), allowed_schemes=_SAFE_MEDIA_SCHEMES)
        if not href:
            continue
        mime_type = (link_el.get("type") or "").strip()
        enclosures.append(
            PreviewEnclosure(
                href=href,
                label=_enclosure_label(href, link_el.get("title"), mime_type),
                mime_type=mime_type,
                size_display=_format_byte_size(link_el.get("length")),
            )
        )
    return enclosures


def sanitize_entry_html(raw_html: str) -> SafeString:
    """Sanitize untrusted entry HTML for browser preview rendering."""
    if not raw_html or not raw_html.strip():
        return mark_safe("")

    normalized = _strip_invalid_xml_chars(raw_html)
    if not normalized.strip():
        return mark_safe("")

    try:
        # Wrap in a sentinel container so plain text or multi-root fragments parse
        # cleanly without lxml raising ParserError or dropping siblings.
        wrapper = lxml_html.fragment_fromstring(normalized, create_parent="div")
        cleaned = _HTML_CLEANER.clean_html(wrapper)

        for el in cleaned.iter():
            if not isinstance(el.tag, str):
                continue
            tag = el.tag.lower()

            # Defense-in-depth attribute pass: strip event handlers, styles,
            # classes/IDs, srcset, and validate every remaining URL-bearing attribute.
            for attr in list(el.attrib):
                attr_lower = attr.lower()
                if attr_lower.startswith("on") or attr_lower in {
                    "class",
                    "id",
                    "style",
                    "srcset",
                    "formaction",
                    "action",
                    "xlink:href",
                    "ping",
                }:
                    del el.attrib[attr]
                    continue

                if attr_lower == "href":
                    safe = _safe_url(el.attrib[attr])
                    if safe:
                        el.attrib[attr] = safe
                    else:
                        del el.attrib[attr]
                elif attr_lower in {"src", "poster"}:
                    safe = _safe_media_url(
                        el.attrib[attr],
                        allow_data_image=(tag == "img" and attr_lower == "src"),
                    )
                    if safe:
                        el.attrib[attr] = safe
                    else:
                        del el.attrib[attr]

            if tag == "a":
                if el.get("href"):
                    el.set("target", "_blank")
                    el.set("rel", "noopener noreferrer")
                else:
                    el.attrib.pop("target", None)
                    el.attrib.pop("rel", None)

        parts: list[str] = []
        if cleaned.text:
            parts.append(escape(cleaned.text))
        for child in cleaned:
            parts.append(lxml_html.tostring(child, encoding="unicode", method="html"))
        return mark_safe("".join(parts))
    except (ValueError, etree.LxmlError) as exc:
        logger.warning(
            "Failed to sanitize entry HTML; falling back to escaped text: %s", exc
        )
        return mark_safe(f"<p>{escape(normalized.strip())}</p>")


def _extract_entry_content(entry_el: etree._Element) -> SafeString:
    content_el = _find_child(entry_el, "content")
    if content_el is None or (
        not (content_el.text and content_el.text.strip()) and len(content_el) == 0
    ):
        content_el = _find_child(entry_el, "summary")
    if content_el is None:
        return mark_safe("")

    content_type = (content_el.get("type") or "text").strip().lower()
    if content_type == "html":
        return sanitize_entry_html(content_el.text or "")
    if content_type == "xhtml":
        raw = "".join(
            etree.tostring(child, encoding="unicode", method="html")
            for child in content_el
        )
        return sanitize_entry_html(raw)

    text = (content_el.text or "").strip()
    if not text:
        return mark_safe("")
    return mark_safe(f"<p>{escape(text)}</p>")


def parse_atom_for_preview(atom_xml: str | bytes) -> PreviewFeed:
    """Parse an Atom feed XML string into a structured ``PreviewFeed``."""
    raw_bytes = atom_xml.encode("utf-8") if isinstance(atom_xml, str) else atom_xml
    root = etree.fromstring(raw_bytes, parser=_SAFE_XML_PARSER)

    raw_updated = _child_text(root, "updated")
    entries: list[PreviewEntry] = []

    for entry_el in _find_children(root, "entry"):
        raw_ts = _child_text(entry_el, "updated") or _child_text(entry_el, "published")
        author_el = _find_child(entry_el, "author")
        author = _child_text(author_el, "name") if author_el is not None else ""
        if author == "Unknown":
            author = ""

        entries.append(
            PreviewEntry(
                title=_child_text(entry_el, "title") or "(Untitled)",
                link=_extract_alternate_link(entry_el),
                timestamp=_format_timestamp(raw_ts),
                raw_timestamp=raw_ts,
                author=author,
                content_html=_extract_entry_content(entry_el),
                enclosures=_extract_enclosures(entry_el),
            )
        )

    return PreviewFeed(
        title=_child_text(root, "title") or "RSS Feed",
        subtitle=_child_text(root, "subtitle"),
        link=_extract_alternate_link(root),
        updated=_format_timestamp(raw_updated),
        raw_updated=raw_updated,
        entries=entries,
    )


def _normalize_netloc(netloc: str, scheme: str = "") -> str:
    """Lowercase host[:port] and drop default ports (80 for http, 443 for https)."""
    value = (netloc or "").strip().lower()
    if not value:
        return ""
    if scheme == "http" and value.endswith(":80"):
        return value[:-3]
    if scheme == "https" and value.endswith(":443"):
        return value[:-4]
    return value


def build_subscribe_url(request, feed_url: str | None) -> str | None:
    """Build the full absolute subscription URL shown in the preview header.

    Uses ``settings.SITE_URL`` when configured to a non-default origin (so
    deployments behind TLS-terminating proxies show ``https://...`` just like
    OPML export); otherwise falls back to ``request.build_absolute_uri()`` so
    local and LAN deployments display the reachable host.
    """
    if not feed_url:
        return None
    site_url = (getattr(settings, "SITE_URL", "") or "").strip().rstrip("/")
    if site_url and site_url.lower() != _DEFAULT_SITE_URL:
        return f"{site_url}{feed_url}"
    if request is not None:
        try:
            return request.build_absolute_uri(feed_url)
        except DisallowedHost:
            pass
    return f"{site_url}{feed_url}" if site_url else feed_url


def _is_self_feed_link(
    link: str,
    feed_url: str | None,
    subscribe_url: str | None,
) -> bool:
    """Return True when ``link`` points back to this feed's own Atom endpoint."""
    if not link or not feed_url:
        return False
    try:
        parsed = urlsplit(link)
    except ValueError:
        return False
    if parsed.path.rstrip("/") != feed_url.rstrip("/"):
        return False

    link_netloc = _normalize_netloc(parsed.netloc, parsed.scheme.lower())
    own_netlocs: set[str] = set()
    for candidate in (subscribe_url, getattr(settings, "SITE_URL", "")):
        if not candidate:
            continue
        try:
            c_parsed = urlsplit(candidate)
        except ValueError:
            continue
        if c_parsed.netloc:
            own_netlocs.add(_normalize_netloc(c_parsed.netloc, c_parsed.scheme.lower()))
    return link_netloc in own_netlocs


def render_atom_as_html(
    atom_xml: str | bytes,
    *,
    feed_url: str | None = None,
    subscribe_url: str | None = None,
    json_url: str | None = None,
) -> str:
    """Render an Atom XML feed string as a standalone, script-free HTML page."""
    feed = parse_atom_for_preview(atom_xml)
    resolved_subscribe_url = subscribe_url or feed_url
    if _is_self_feed_link(feed.link, feed_url, resolved_subscribe_url):
        feed.link = ""
    return render_to_string(
        "rss/feed_preview.html",
        {
            "feed": feed,
            "feed_url": feed_url,
            "subscribe_url": resolved_subscribe_url,
            "json_url": json_url,
        },
    )

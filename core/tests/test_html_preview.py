"""Tests for server-side same-origin HTML feed previews (replacing client-side XSLT)."""

from datetime import timedelta
from unittest.mock import patch
from urllib.parse import quote

from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone
from lxml import etree

from core.feed_preview import (
    PREVIEW_CSP,
    PREVIEW_VARY_HEADERS,
    build_subscribe_url,
    parse_atom_for_preview,
    render_atom_as_html,
    sanitize_entry_html,
    wants_html_preview,
)
from core.models import Digest, Entry, Feed, OpenAIAgent, Tag


class SanitizeEntryHtmlTests(TestCase):
    def test_empty_and_plain_text_inputs(self):
        self.assertEqual(str(sanitize_entry_html("")), "")
        self.assertEqual(str(sanitize_entry_html("   \n\t  ")), "")
        self.assertEqual(str(sanitize_entry_html("A & B < C")), "A &amp; B &lt; C")

    def test_preserves_safe_formatting_and_media(self):
        raw = (
            "<p>Hello <strong>world</strong></p>"
            '<a href="https://example.com/article">Read more</a>'
            '<img src="https://example.com/pic.png" alt="pic">'
            '<audio controls src="https://example.com/clip.mp3"></audio>'
            '<video controls poster="https://example.com/poster.jpg">'
            '<source src="https://example.com/vid.mp4" type="video/mp4"></video>'
            '<noscript><img src="https://example.com/lazy.png" alt="lazy">'
            "<script>evil()</script></noscript>"
            '<img src="data:image/png;base64,iVBORw0KGgo=" alt="inline">'
            "<pre><code>print(1)</code></pre>"
        )
        cleaned = str(sanitize_entry_html(raw))
        self.assertIn("<p>Hello <strong>world</strong></p>", cleaned)
        self.assertIn('href="https://example.com/article"', cleaned)
        self.assertIn('target="_blank"', cleaned)
        self.assertIn('rel="noopener noreferrer"', cleaned)
        self.assertIn('src="https://example.com/pic.png"', cleaned)
        self.assertIn('<audio controls src="https://example.com/clip.mp3">', cleaned)
        self.assertIn('poster="https://example.com/poster.jpg"', cleaned)
        self.assertIn('src="https://example.com/lazy.png"', cleaned)
        self.assertIn('src="data:image/png;base64,iVBORw0KGgo="', cleaned)
        self.assertIn("<pre><code>print(1)</code></pre>", cleaned)
        self.assertNotIn("<noscript", cleaned.lower())
        self.assertNotIn("<script", cleaned.lower())
        self.assertNotIn("evil()", cleaned)

    def test_strips_active_and_dangerous_elements(self):
        raw = (
            "<p>Safe</p>"
            "<script>alert(1)</script>"
            "<style>body { display: none; }</style>"
            '<iframe src="https://evil.example/"></iframe>'
            '<form action="https://evil.example/"><input name="pw"></form>'
            '<svg onload="alert(1)"><script>alert(2)</script></svg>'
            "<math><mtext>x</mtext></math>"
            "<!-- secret comment -->"
        )
        cleaned = str(sanitize_entry_html(raw))
        self.assertIn("<p>Safe</p>", cleaned)
        for forbidden in (
            "<script",
            "alert(",
            "<style",
            "<iframe",
            "<form",
            "<input",
            "<svg",
            "<math",
            "secret comment",
        ):
            self.assertNotIn(forbidden, cleaned.lower())

    def test_strips_event_handlers_inline_styles_and_unsafe_urls(self):
        raw = (
            '<p style="position:fixed" onclick="alert(1)">Text</p>'
            '<a href="javascript:alert(1)">JS link</a>'
            '<a href="java\tscript:alert(1)">Obfuscated JS</a>'
            '<a href="data:text/html,<script>alert(1)</script>">Data link</a>'
            '<img src="https://example.com/a.jpg" onerror="alert(1)" srcset="javascript:alert(1)">'
            '<img src="data:image/svg+xml;base64,PHN2Zz4=" alt="svg">'
            '<video controls poster="javascript:alert(1)" src="javascript:alert(2)"></video>'
        )
        cleaned = str(sanitize_entry_html(raw))
        self.assertIn("Text", cleaned)
        self.assertIn("JS link", cleaned)
        self.assertIn('src="https://example.com/a.jpg"', cleaned)
        for forbidden in (
            "onclick",
            "onerror",
            "style=",
            "srcset",
            "javascript:",
            "data:text/html",
            "data:image/svg+xml",
        ):
            self.assertNotIn(forbidden, cleaned.lower())

    def test_strips_control_chars_and_numeric_entities_without_crashing(self):
        raw = (
            "hello &#12; world &#x0b; &#0; \x0c "
            "<p>keep &#169; &amp;#12;</p>"
            "<noscript>tail&#11;</noscript>"
            "&&#12;#12;<p>nested ok</p>"
            "&#99999999;"
        )
        cleaned = str(sanitize_entry_html(raw))
        self.assertIn("hello  world", cleaned)
        self.assertIn("keep ©", cleaned)
        self.assertIn("&amp;#12;", cleaned)
        self.assertIn("tail", cleaned)
        # Nested invalid entity must not collapse into another &#12; and
        # trigger the escaped-text fallback.
        self.assertIn("<p>nested ok</p>", cleaned)
        self.assertNotIn("\x0c", cleaned)
        self.assertNotIn("\x0b", cleaned)

    def test_sanitizer_exception_falls_back_to_escaped_text(self):
        with patch(
            "core.feed_preview.lxml_html.fragment_fromstring",
            side_effect=ValueError("simulated parser error"),
        ):
            cleaned = str(sanitize_entry_html("<p>Bad & <script>x</script></p>"))
        self.assertEqual(
            cleaned,
            "<p>&lt;p&gt;Bad &amp; &lt;script&gt;x&lt;/script&gt;&lt;/p&gt;</p>",
        )

    def test_handles_plaintext_and_legacy_raw_text_tags(self):
        raw = (
            "<p>Before</p>"
            "<noembed>hidden embed</noembed>"
            "<noframes>hidden frames</noframes>"
            "<xmp><b>raw xmp</b></xmp>"
            "<listing>listing line</listing>"
            "<plaintext>swallowed tail <b>bold</b>"
        )
        cleaned = str(sanitize_entry_html(raw))
        self.assertIn("<p>Before</p>", cleaned)
        self.assertIn("&lt;b&gt;raw xmp&lt;/b&gt;", cleaned)
        self.assertIn("listing line", cleaned)
        for forbidden in (
            "<plaintext",
            "swallowed tail",
            "</body>",
            "&lt;/body&gt;",
            "<noembed",
            "hidden embed",
            "<noframes",
            "hidden frames",
            "<xmp",
            "<listing",
        ):
            self.assertNotIn(forbidden, cleaned.lower())

    def test_normalizes_protocol_relative_urls_and_preserves_fragment_anchors(self):
        raw = (
            '<img src="//cdn.example.com/a.png" alt="cdn">'
            '<img src="///evil.example/bad.png" alt="bad">'
            '<img src="#not-an-image" alt="frag">'
            '<a href="//example.com/path">Proto link</a>'
            '<a href="///bad">Bad proto</a>'
            '<a href="#fn1" target="_blank">[1]</a>'
            '<p id="fn1">Footnote</p>'
        )
        cleaned = str(sanitize_entry_html(raw))
        self.assertIn('src="https://cdn.example.com/a.png"', cleaned)
        self.assertNotIn("evil.example", cleaned)
        self.assertNotIn('src="#not-an-image"', cleaned)
        self.assertIn('href="https://example.com/path"', cleaned)
        self.assertNotIn('href="///bad"', cleaned)
        self.assertIn('<a href="#fn1">[1]</a>', cleaned)
        self.assertIn('<p id="fn1">Footnote</p>', cleaned)


class WantsHtmlPreviewTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_same_origin_fetch_metadata_triggers_preview(self):
        req = self.factory.get(
            "/rss/example-feed",
            HTTP_ACCEPT="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            HTTP_SEC_FETCH_SITE="same-origin",
            HTTP_SEC_FETCH_MODE="navigate",
            HTTP_SEC_FETCH_DEST="document",
        )
        self.assertTrue(wants_html_preview(req))

    def test_same_host_referer_triggers_preview_without_fetch_metadata(self):
        req = self.factory.get(
            "/rss/example-feed",
            HTTP_HOST="rssbox.local:8080",
            HTTP_ACCEPT="text/html,application/xhtml+xml,*/*;q=0.8",
            HTTP_REFERER="http://rssbox.local:8080/core/feed/",
        )
        self.assertTrue(wants_html_preview(req))

    @override_settings(SITE_URL="https://rss.example.com")
    def test_referer_matching_site_url_behind_proxy_triggers_preview(self):
        req = self.factory.get(
            "/rss/example-feed",
            HTTP_HOST="127.0.0.1:8000",
            HTTP_ACCEPT="text/html,*/*;q=0.8",
            HTTP_REFERER="https://rss.example.com/core/feed/",
        )
        self.assertTrue(wants_html_preview(req))

    def test_spoofed_browser_rss_fetcher_receives_xml(self):
        # Mirrors the exact browser-like headers sent by core/tasks/fetch_feeds.py
        req = self.factory.get(
            "/rss/example-feed",
            HTTP_ACCEPT="text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            HTTP_SEC_FETCH_DEST="document",
            HTTP_SEC_FETCH_MODE="navigate",
            HTTP_SEC_FETCH_SITE="none",
            HTTP_SEC_FETCH_USER="?1",
        )
        self.assertFalse(wants_html_preview(req))

    def test_explicit_non_same_origin_fetch_site_does_not_fallback_to_referer(self):
        # Anti-hotlink scrapers may forge same-host Referer while sending
        # Sec-Fetch-Site: none (or cross-site).
        for fetch_site in ("none", "cross-site"):
            with self.subTest(fetch_site=fetch_site):
                req = self.factory.get(
                    "/rss/example-feed",
                    HTTP_HOST="rssbox.local:8000",
                    HTTP_ACCEPT="text/html,application/xml;q=0.9,*/*;q=0.8",
                    HTTP_REFERER="http://rssbox.local:8000/core/feed/",
                    HTTP_SEC_FETCH_SITE=fetch_site,
                    HTTP_SEC_FETCH_MODE="navigate",
                    HTTP_SEC_FETCH_DEST="document",
                )
                self.assertFalse(wants_html_preview(req))

    def test_cross_origin_referer_receives_xml(self):
        req = self.factory.get(
            "/rss/example-feed",
            HTTP_HOST="rssbox.local:8000",
            HTTP_ACCEPT="text/html,application/xml;q=0.9,*/*;q=0.8",
            HTTP_REFERER="https://inoreader.com/feed/123",
            HTTP_SEC_FETCH_SITE="cross-site",
            HTTP_SEC_FETCH_MODE="navigate",
            HTTP_SEC_FETCH_DEST="document",
        )
        self.assertFalse(wants_html_preview(req))

    def test_same_origin_xhr_or_fetch_receives_xml(self):
        req = self.factory.get(
            "/rss/example-feed",
            HTTP_HOST="rssbox.local:8000",
            HTTP_ACCEPT="text/html,*/*",
            HTTP_REFERER="http://rssbox.local:8000/core/feed/",
            HTTP_SEC_FETCH_SITE="same-origin",
            HTTP_SEC_FETCH_MODE="cors",
            HTTP_SEC_FETCH_DEST="empty",
        )
        self.assertFalse(wants_html_preview(req))

    def test_plain_rss_reader_receives_xml(self):
        req = self.factory.get(
            "/rss/example-feed",
            HTTP_ACCEPT="application/atom+xml, application/xml, */*",
        )
        self.assertFalse(wants_html_preview(req))

    def test_explicit_query_overrides_always_win(self):
        # ?format=xml forces XML even on same-origin admin click
        req_xml = self.factory.get(
            "/rss/example-feed?format=xml",
            HTTP_HOST="rssbox.local:8000",
            HTTP_ACCEPT="text/html,*/*",
            HTTP_REFERER="http://rssbox.local:8000/core/feed/",
            HTTP_SEC_FETCH_SITE="same-origin",
            HTTP_SEC_FETCH_MODE="navigate",
            HTTP_SEC_FETCH_DEST="document",
        )
        self.assertFalse(wants_html_preview(req_xml))

        # ?format=html forces HTML even without Referer or Accept: text/html
        req_html = self.factory.get("/rss/example-feed?format=html")
        self.assertTrue(wants_html_preview(req_html))


class ParseAtomPreviewTests(TestCase):
    def test_parses_metadata_entries_and_enclosures(self):
        atom_xml = """<?xml version='1.0' encoding='utf-8'?>
        <feed xmlns="http://www.w3.org/2005/Atom">
            <id>urn:test</id>
            <title>Tech &amp; Science</title>
            <subtitle>Daily highlights</subtitle>
            <link href="https://example.com" rel="alternate"/>
            <updated>2026-11-19T10:30:00+00:00</updated>
            <entry>
                <id>urn:entry-1</id>
                <title>First Post</title>
                <link href="https://example.com/post-1" rel="alternate"/>
                <link href="https://example.com/audio.mp3" rel="enclosure" type="audio/mpeg" length="2048"/>
                <link href="javascript:alert(1)" rel="enclosure" type="text/html"/>
                <author><name>Alice</name></author>
                <updated>2026-11-19T09:15:00+00:00</updated>
                <content type="html">&lt;p&gt;Body&lt;/p&gt;</content>
            </entry>
            <entry>
                <id>urn:entry-2</id>
                <title>Plain Text Entry</title>
                <author><name>Unknown</name></author>
                <published>2026-11-18T08:00:00Z</published>
                <summary type="text">Plain &lt;b&gt;text&lt;/b&gt;</summary>
            </entry>
        </feed>"""

        feed = parse_atom_for_preview(atom_xml)
        self.assertEqual(feed.title, "Tech & Science")
        self.assertEqual(feed.subtitle, "Daily highlights")
        self.assertEqual(feed.link, "https://example.com")
        self.assertEqual(feed.updated, "2026-11-19 10:30")
        self.assertEqual(len(feed.entries), 2)

        first = feed.entries[0]
        self.assertEqual(first.title, "First Post")
        self.assertEqual(first.link, "https://example.com/post-1")
        self.assertEqual(first.author, "Alice")
        self.assertEqual(first.timestamp, "2026-11-19 09:15")
        self.assertIn("<p>Body</p>", str(first.content_html))
        self.assertEqual(len(first.enclosures), 1)
        self.assertEqual(first.enclosures[0].href, "https://example.com/audio.mp3")
        self.assertEqual(first.enclosures[0].label, "audio.mp3")
        self.assertEqual(first.enclosures[0].mime_type, "audio/mpeg")
        self.assertEqual(first.enclosures[0].size_display, "2 KB")

        second = feed.entries[1]
        self.assertEqual(second.author, "")
        self.assertEqual(second.timestamp, "2026-11-18 08:00")
        self.assertEqual(
            str(second.content_html), "<p>Plain &lt;b&gt;text&lt;/b&gt;</p>"
        )

    def test_malformed_xml_raises(self):
        with self.assertRaises(etree.XMLSyntaxError):
            render_atom_as_html("<feed><unclosed>")

    def test_xhtml_content_is_sanitized(self):
        atom_xml = """<?xml version='1.0' encoding='utf-8'?>
        <feed xmlns="http://www.w3.org/2005/Atom">
            <title>XHTML Feed</title>
            <entry>
                <title>XHTML Entry</title>
                <content type="xhtml">
                    <div xmlns="http://www.w3.org/1999/xhtml">
                        <p>Clean paragraph</p>
                        <script>alert(1)</script>
                    </div>
                </content>
            </entry>
        </feed>"""
        feed = parse_atom_for_preview(atom_xml)
        content = str(feed.entries[0].content_html)
        self.assertIn("Clean paragraph", content)
        self.assertNotIn("<script", content.lower())
        self.assertNotIn("alert(", content)


_ADMIN_CLICK_HEADERS = {
    "HTTP_ACCEPT": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "HTTP_SEC_FETCH_SITE": "same-origin",
    "HTTP_SEC_FETCH_MODE": "navigate",
    "HTTP_SEC_FETCH_DEST": "document",
    "HTTP_REFERER": "http://testserver/core/feed/",
}
_SPOOFED_FETCHER_HEADERS = {
    "HTTP_ACCEPT": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "HTTP_SEC_FETCH_SITE": "none",
    "HTTP_SEC_FETCH_MODE": "navigate",
    "HTTP_SEC_FETCH_DEST": "document",
}


class HtmlPreviewRoutesTests(TestCase):
    def setUp(self):
        cache.clear()
        self.now = timezone.now()
        self.tag = Tag.objects.create(name="Tech News", slug="tech-news")
        self.chinese_tag = Tag.objects.create(name="深度学习")
        self.feed = Feed.objects.create(
            name="Example Feed",
            subtitle="Feed subtitle",
            slug="example-feed",
            feed_url="https://example.com/rss.xml",
            link="https://example.com",
            translate_title=True,
            translate_content=True,
            translation_status=True,
            last_translate=self.now,
            last_fetch=self.now,
        )
        self.feed.tags.add(self.tag, self.chinese_tag)
        self.entry = Entry.objects.create(
            feed=self.feed,
            guid="entry-1",
            link="https://example.com/posts/1",
            author="Jane Doe",
            pubdate=self.now - timedelta(minutes=5),
            updated=self.now - timedelta(minutes=5),
            original_title="Original <script>alert(1)</script> Title",
            translated_title="翻译标题",
            original_content='<p>Original body</p><script>alert("xss")</script>',
            translated_content=(
                '<p>翻译正文</p><img src="https://example.com/img.png" onerror="alert(1)">'
                '<a href="javascript:alert(1)">bad</a>'
            ),
            enclosures_xml=(
                '<enclosures><enclosure href="https://example.com/ep1.mp3" '
                'type="audio/mpeg" length="1048576"/></enclosures>'
            ),
        )

    def tearDown(self):
        cache.clear()

    def _assert_vary_contains_preview_headers(self, response):
        vary_tokens = {
            token.strip().lower() for token in response.get("Vary", "").split(",")
        }
        for header in PREVIEW_VARY_HEADERS:
            self.assertIn(header.lower(), vary_tokens)

    def _assert_preview_headers(self, response):
        self.assertEqual(response["Content-Type"], "text/html; charset=utf-8")
        self.assertEqual(response["Content-Security-Policy"], PREVIEW_CSP)
        self.assertEqual(response["Referrer-Policy"], "same-origin")
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertNotIn("ETag", response)
        self.assertNotIn("Last-Modified", response)
        self._assert_vary_contains_preview_headers(response)

    def test_xml_feed_has_no_xslt_and_sets_vary(self):
        for headers in ({}, _SPOOFED_FETCHER_HEADERS):
            with self.subTest(headers=headers):
                response = self.client.get(f"/rss/{self.feed.slug}", **headers)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response["Content-Type"], "application/xml; charset=utf-8"
                )
                self._assert_vary_contains_preview_headers(response)
                body = b"".join(response.streaming_content).decode("utf-8")
                self.assertNotIn("xml-stylesheet", body)
                self.assertNotIn("rss.xsl", body)
                self.assertIn("翻译标题", body)

    def test_same_origin_click_renders_sanitized_html_preview(self):
        response = self.client.get(f"/rss/{self.feed.slug}", **_ADMIN_CLICK_HEADERS)
        self.assertEqual(response.status_code, 200)
        self._assert_preview_headers(response)
        html = response.content.decode("utf-8")
        self.assertIn("Example Feed", html)
        self.assertIn("Feed subtitle", html)
        self.assertIn("翻译标题", html)
        self.assertIn("<p>翻译正文</p>", html)
        self.assertIn("ep1.mp3", html)
        self.assertIn("1 MB", html)
        self.assertIn(
            f'rel="alternate" type="application/atom+xml" title="Example Feed" href="/rss/{self.feed.slug}"',
            html,
        )
        self.assertIn(f'href="/rss/{self.feed.slug}?format=xml"', html)
        self.assertIn(f'href="/rss/json/{self.feed.slug}"', html)
        self.assertNotIn("<script", html.lower())
        self.assertNotIn("onerror", html.lower())
        self.assertNotIn("javascript:", html.lower())

    def test_query_format_overrides_on_same_url(self):
        # Clicking "Atom XML" (?format=xml) from inside the HTML preview returns XML
        xml_resp = self.client.get(
            f"/rss/{self.feed.slug}?format=xml", **_ADMIN_CLICK_HEADERS
        )
        self.assertEqual(xml_resp.status_code, 200)
        self.assertEqual(xml_resp["Content-Type"], "application/xml; charset=utf-8")

        # Direct bookmark with ?format=html renders HTML without Referer, and
        # shows a copyable full absolute URL without ?format=html.
        html_resp = self.client.get(f"/rss/{self.feed.slug}?format=html")
        self.assertEqual(html_resp.status_code, 200)
        self._assert_preview_headers(html_resp)
        html = html_resp.content.decode("utf-8")
        self.assertIn(
            f'<code class="feed-path">http://testserver/rss/{self.feed.slug}</code>',
            html,
        )
        self.assertIn(">Website</a>", html)
        self.assertIn('<meta name="referrer" content="same-origin">', html)

    @override_settings(SITE_URL="https://rss.example.com/")
    def test_subscribe_url_prefers_configured_site_url(self):
        html_resp = self.client.get(f"/rss/{self.feed.slug}?format=html")
        self.assertEqual(html_resp.status_code, 200)
        html = html_resp.content.decode("utf-8")
        self.assertIn(
            f'<code class="feed-path">https://rss.example.com/rss/{self.feed.slug}</code>',
            html,
        )
        self.assertEqual(
            build_subscribe_url(None, "/rss/demo"),
            "https://rss.example.com/rss/demo",
        )

    def test_entry_with_control_char_entity_still_renders_200(self):
        Entry.objects.create(
            feed=self.feed,
            guid="entry-ctrl",
            link="https://example.com/posts/ctrl",
            pubdate=self.now,
            updated=self.now,
            original_title="PDF Extract",
            translated_title="PDF 摘录",
            translated_content="Page 1 &#12; Page 2 <plaintext>should not eat footer",
        )
        cache.clear()
        resp = self.client.get(f"/rss/{self.feed.slug}", **_ADMIN_CLICK_HEADERS)
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")
        self.assertIn("Page 1  Page 2", html)
        self.assertIn("</html>", html)
        self.assertNotIn("<plaintext", html.lower())

    def test_conditional_headers_from_xml_never_short_circuit_html_preview(self):
        xml_resp = self.client.get(f"/rss/{self.feed.slug}")
        self.assertEqual(xml_resp.status_code, 200)
        etag = xml_resp["ETag"]
        last_modified = xml_resp["Last-Modified"]

        # Same-origin HTML preview with XML's ETag & Last-Modified must NOT get 304
        html_resp = self.client.get(
            f"/rss/{self.feed.slug}",
            HTTP_IF_NONE_MATCH=etag,
            HTTP_IF_MODIFIED_SINCE=last_modified,
            **_ADMIN_CLICK_HEADERS,
        )
        self.assertEqual(html_resp.status_code, 200)
        self._assert_preview_headers(html_resp)

        # RSS reader revalidating XML with the same ETag still gets 304
        reval_resp = self.client.get(
            f"/rss/{self.feed.slug}",
            HTTP_IF_NONE_MATCH=etag,
        )
        self.assertEqual(reval_resp.status_code, 304)

    def test_json_endpoint_never_returns_html(self):
        response = self.client.get(
            f"/rss/json/{self.feed.slug}?format=html", **_ADMIN_CLICK_HEADERS
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")

    def test_proxy_feed_preview_and_xml(self):
        # Reader gets XML
        xml_resp = self.client.get(f"/rss/proxy/{self.feed.slug}")
        self.assertEqual(xml_resp.status_code, 200)
        self.assertEqual(xml_resp["Content-Type"], "application/xml; charset=utf-8")

        # Admin click gets HTML preview
        html_resp = self.client.get(
            f"/rss/proxy/{self.feed.slug}", **_ADMIN_CLICK_HEADERS
        )
        self.assertEqual(html_resp.status_code, 200)
        self._assert_preview_headers(html_resp)
        html = html_resp.content.decode("utf-8")
        self.assertIn("Original &lt;script&gt;alert(1)&lt;/script&gt; Title", html)
        self.assertIn("<p>Original body</p>", html)
        self.assertNotIn("<script", html.lower())
        self.assertIn(f'href="/rss/proxy/{self.feed.slug}?format=xml"', html)
        self.assertNotIn(f"/rss/json/{self.feed.slug}", html)

    def test_tag_and_tag_proxy_previews(self):
        resp = self.client.get(f"/rss/tag/{self.tag.slug}", **_ADMIN_CLICK_HEADERS)
        self.assertEqual(resp.status_code, 200)
        self._assert_preview_headers(resp)
        html = resp.content.decode("utf-8")
        self.assertIn("翻译标题", html)
        self.assertIn(f'href="/rss/tag/{self.tag.slug}?format=xml"', html)
        self.assertIn(f'href="/rss/tag/json/{self.tag.slug}"', html)

        proxy_resp = self.client.get(
            f"/rss/tag/proxy/{self.tag.slug}", **_ADMIN_CLICK_HEADERS
        )
        self.assertEqual(proxy_resp.status_code, 200)
        self._assert_preview_headers(proxy_resp)
        proxy_html = proxy_resp.content.decode("utf-8")
        self.assertIn("<p>Original body</p>", proxy_html)
        self.assertIn(f'href="/rss/tag/proxy/{self.tag.slug}?format=xml"', proxy_html)
        self.assertNotIn(f"/rss/tag/json/{self.tag.slug}", proxy_html)

    def test_chinese_tag_preview(self):
        encoded = quote(self.chinese_tag.slug)
        resp = self.client.get(f"/rss/tag/{encoded}", **_ADMIN_CLICK_HEADERS)
        self.assertEqual(resp.status_code, 200)
        self._assert_preview_headers(resp)
        html = resp.content.decode("utf-8")
        self.assertIn(f'href="/rss/tag/{encoded}?format=xml"', html)
        self.assertIn(f'href="/rss/tag/json/{encoded}"', html)

    def test_digest_preview_and_public_view_intact(self):
        agent = OpenAIAgent.objects.create(
            name="Summarizer", api_key="test", valid=True
        )
        digest = Digest.objects.create(
            name="Daily Briefing",
            slug="daily-briefing",
            description="Top stories",
            summarizer=agent,
            last_generated=self.now,
        )
        digest_feed = digest.get_digest_feed()
        Entry.objects.create(
            feed=digest_feed,
            guid="digest-1",
            link=f"http://localhost:8000/rss/digest/view/{digest.slug}",
            original_title="Daily Briefing 2026-11-19",
            ai_summary="## Highlights\n\n- Important news",
            pubdate=self.now,
        )

        # RSS reader gets XML
        xml_resp = self.client.get(f"/rss/digest/{digest.slug}")
        self.assertEqual(xml_resp.status_code, 200)
        self.assertEqual(xml_resp["Content-Type"], "application/xml; charset=utf-8")
        digest_etag = xml_resp["ETag"]

        # Same-origin click gets HTML preview (not 304 even with XML ETag)
        html_resp = self.client.get(
            f"/rss/digest/{digest.slug}",
            HTTP_IF_NONE_MATCH=digest_etag,
            **_ADMIN_CLICK_HEADERS,
        )
        self.assertEqual(html_resp.status_code, 200)
        self._assert_preview_headers(html_resp)
        html = html_resp.content.decode("utf-8")
        self.assertIn("Daily Briefing", html)
        self.assertIn("Important news", html)
        self.assertIn(f'href="/rss/digest/{digest.slug}?format=xml"', html)
        self.assertIn(f'href="/rss/digest/json/{digest.slug}"', html)
        self.assertIn(
            f'<code class="feed-path">http://testserver/rss/digest/{digest.slug}</code>',
            html,
        )
        # Digest's feed.link points back to /rss/digest/<slug> itself, so the
        # redundant self-referencing Website button must be omitted.
        self.assertNotIn(">Website</a>", html)

        # Existing /rss/digest/view/<slug> continues to work unchanged
        view_resp = self.client.get(f"/rss/digest/view/{digest.slug}")
        self.assertEqual(view_resp.status_code, 200)
        self.assertIn("Important news", view_resp.content.decode("utf-8"))

    def test_make_response_html_empty_returns_404(self):
        from core.views import _make_response

        response = _make_response(None, "empty", "html")
        self.assertEqual(response.status_code, 404)

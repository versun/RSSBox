"""Tests for server-side HTML feed previews (?format=html, replacing XSLT)."""

from datetime import timedelta
from unittest.mock import patch
from urllib.parse import quote

from django.contrib.admin.sites import AdminSite
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from lxml import etree

from core.admin.digest_admin import DigestAdmin
from core.feed_preview import (
    PREVIEW_CSP,
    build_subscribe_url,
    parse_atom_for_preview,
    render_atom_as_html,
    sanitize_entry_html,
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

    def test_strips_event_handlers_styles_classes_ids_and_unsafe_urls(self):
        raw = (
            '<div class="container pill" id="hijack">'
            '<p style="position:fixed" onclick="alert(1)">Text</p>'
            '<a href="javascript:alert(1)">JS link</a>'
            '<a href="java\tscript:alert(1)">Obfuscated JS</a>'
            '<a href="data:text/html,<script>alert(1)</script>">Data link</a>'
            '<img src="https://example.com/a.jpg" onerror="alert(1)" srcset="javascript:alert(1)">'
            '<img src="data:image/svg+xml;base64,PHN2Zz4=" alt="svg">'
            '<video controls poster="javascript:alert(1)" src="javascript:alert(2)"></video>'
            "</div>"
        )
        cleaned = str(sanitize_entry_html(raw))
        self.assertIn("<div><p>Text</p>", cleaned)
        self.assertIn("JS link", cleaned)
        self.assertIn('src="https://example.com/a.jpg"', cleaned)
        for forbidden in (
            "class=",
            "id=",
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

    def test_normalizes_protocol_relative_urls_and_strips_fragment_anchors(self):
        raw = (
            '<img src="//cdn.example.com/a.png" alt="cdn">'
            '<img src="///evil.example/bad.png" alt="bad">'
            '<img src="#not-an-image" alt="frag">'
            '<a href="//example.com/path">Proto link</a>'
            '<a href="///bad">Bad proto</a>'
            '<a href="#fn1" target="_blank">[1]</a>'
        )
        cleaned = str(sanitize_entry_html(raw))
        self.assertIn('src="https://cdn.example.com/a.png"', cleaned)
        self.assertNotIn("evil.example", cleaned)
        self.assertNotIn('src="#not-an-image"', cleaned)
        self.assertIn('href="https://example.com/path"', cleaned)
        self.assertNotIn('href="///bad"', cleaned)
        self.assertNotIn('href="#fn1"', cleaned)
        self.assertIn("<a>[1]</a>", cleaned)


@override_settings(TIME_ZONE="UTC")
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
                        <p class="container">Clean paragraph</p>
                        <script>alert(1)</script>
                    </div>
                </content>
            </entry>
        </feed>"""
        feed = parse_atom_for_preview(atom_xml)
        content = str(feed.entries[0].content_html)
        self.assertIn("<p>Clean paragraph</p>", content)
        self.assertNotIn("class=", content)
        self.assertNotIn("<script", content.lower())
        self.assertNotIn("alert(", content)


_BROWSER_HEADERS = {
    "HTTP_ACCEPT": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "HTTP_SEC_FETCH_SITE": "same-origin",
    "HTTP_SEC_FETCH_MODE": "navigate",
    "HTTP_SEC_FETCH_DEST": "document",
    "HTTP_REFERER": "http://testserver/core/feed/",
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
                '<p class="pill">翻译正文</p>'
                '<img src="https://example.com/img.png" onerror="alert(1)">'
                '<a href="javascript:alert(1)">bad</a>'
            ),
            enclosures_xml=(
                '<enclosures><enclosure href="https://example.com/ep1.mp3" '
                'type="audio/mpeg" length="1048576"/></enclosures>'
            ),
        )

    def tearDown(self):
        cache.clear()

    def _assert_preview_headers(self, response):
        self.assertEqual(response["Content-Type"], "text/html; charset=utf-8")
        self.assertEqual(response["Content-Security-Policy"], PREVIEW_CSP)
        self.assertIn(
            "sandbox allow-popups allow-popups-to-escape-sandbox allow-downloads",
            response["Content-Security-Policy"],
        )
        self.assertEqual(response["Referrer-Policy"], "no-referrer")

    def test_bare_xml_endpoint_always_returns_pure_atom_xml(self):
        # Even with full browser / same-origin headers, bare /rss/<slug> always
        # serves pure Atom XML without xml-stylesheet.
        for headers in ({}, _BROWSER_HEADERS):
            with self.subTest(headers=headers):
                response = self.client.get(f"/rss/{self.feed.slug}", **headers)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response["Content-Type"], "application/xml; charset=utf-8"
                )
                body = b"".join(response.streaming_content).decode("utf-8")
                self.assertNotIn("xml-stylesheet", body)
                self.assertNotIn("rss.xsl", body)
                self.assertIn("翻译标题", body)

    def test_explicit_format_html_renders_sanitized_preview(self):
        response = self.client.get(f"/rss/{self.feed.slug}?format=html")
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
            f'<code class="feed-path">http://testserver/rss/{self.feed.slug}</code>',
            html,
        )
        self.assertIn(
            f'rel="alternate" type="application/atom+xml" title="Example Feed" href="/rss/{self.feed.slug}"',
            html,
        )
        self.assertIn(
            f'<a class="pill" href="/rss/{self.feed.slug}">Atom XML</a>', html
        )
        self.assertIn(f'href="/rss/json/{self.feed.slug}"', html)
        self.assertIn(">Website</a>", html)
        self.assertIn('<meta name="referrer" content="no-referrer">', html)
        self.assertNotIn("<script", html.lower())
        self.assertNotIn("onerror", html.lower())
        self.assertNotIn("javascript:", html.lower())

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
        resp = self.client.get(f"/rss/{self.feed.slug}?format=html")
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")
        self.assertIn("Page 1  Page 2", html)
        self.assertIn("</html>", html)
        self.assertNotIn("<plaintext", html.lower())

    def test_distinct_html_etag_supports_conditional_requests_without_collision(self):
        xml_resp = self.client.get(f"/rss/{self.feed.slug}")
        self.assertEqual(xml_resp.status_code, 200)
        xml_etag = xml_resp["ETag"]

        # HTML preview has its own -html ETag and does not 304 on XML's ETag
        html_resp = self.client.get(
            f"/rss/{self.feed.slug}?format=html",
            HTTP_IF_NONE_MATCH=xml_etag,
        )
        self.assertEqual(html_resp.status_code, 200)
        self._assert_preview_headers(html_resp)
        html_etag = html_resp["ETag"]
        self.assertNotEqual(html_etag, xml_etag)
        self.assertTrue(html_etag.strip('"').endswith("-html"))

        # Revalidating HTML preview with its own ETag returns 304
        html_304 = self.client.get(
            f"/rss/{self.feed.slug}?format=html",
            HTTP_IF_NONE_MATCH=html_etag,
        )
        self.assertEqual(html_304.status_code, 304)

        # Revalidating XML with XML's ETag returns 304
        xml_304 = self.client.get(
            f"/rss/{self.feed.slug}",
            HTTP_IF_NONE_MATCH=xml_etag,
        )
        self.assertEqual(xml_304.status_code, 304)

    def test_weak_and_quoted_proxy_etags_stay_valid_for_html_preview(self):
        for upstream_etag, expected_html_etag in (
            ('W/"abc123"', 'W/"abc123-html"'),
            ('"abc123"', '"abc123-html"'),
        ):
            with self.subTest(upstream_etag=upstream_etag):
                self.feed.etag = upstream_etag
                self.feed.save(update_fields=["etag"])
                html_resp = self.client.get(f"/rss/proxy/{self.feed.slug}?format=html")
                self.assertEqual(html_resp.status_code, 200)
                self.assertEqual(html_resp["ETag"], expected_html_etag)
                revalidated = self.client.get(
                    f"/rss/proxy/{self.feed.slug}?format=html",
                    HTTP_IF_NONE_MATCH=expected_html_etag,
                )
                self.assertEqual(revalidated.status_code, 304)

    def test_render_error_is_not_masked_as_404(self):
        with (
            patch(
                "core.views.render_atom_as_html",
                side_effect=RuntimeError("template boom"),
            ),
            self.assertRaises(RuntimeError),
        ):
            self.client.get(f"/rss/{self.feed.slug}?format=html")

    def test_json_endpoint_never_returns_html(self):
        response = self.client.get(f"/rss/json/{self.feed.slug}?format=html")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")

    def test_proxy_feed_preview_and_xml(self):
        xml_resp = self.client.get(f"/rss/proxy/{self.feed.slug}", **_BROWSER_HEADERS)
        self.assertEqual(xml_resp.status_code, 200)
        self.assertEqual(xml_resp["Content-Type"], "application/xml; charset=utf-8")

        html_resp = self.client.get(f"/rss/proxy/{self.feed.slug}?format=html")
        self.assertEqual(html_resp.status_code, 200)
        self._assert_preview_headers(html_resp)
        html = html_resp.content.decode("utf-8")
        self.assertIn("Original &lt;script&gt;alert(1)&lt;/script&gt; Title", html)
        self.assertIn("<p>Original body</p>", html)
        self.assertNotIn("<script", html.lower())
        self.assertIn(
            f'<a class="pill" href="/rss/proxy/{self.feed.slug}">Atom XML</a>', html
        )
        self.assertNotIn(f"/rss/json/{self.feed.slug}", html)

    def test_tag_and_tag_proxy_previews(self):
        resp = self.client.get(f"/rss/tag/{self.tag.slug}?format=html")
        self.assertEqual(resp.status_code, 200)
        self._assert_preview_headers(resp)
        html = resp.content.decode("utf-8")
        self.assertIn("翻译标题", html)
        self.assertIn(
            f'<a class="pill" href="/rss/tag/{self.tag.slug}">Atom XML</a>', html
        )
        self.assertIn(f'href="/rss/tag/json/{self.tag.slug}"', html)

        proxy_resp = self.client.get(f"/rss/tag/proxy/{self.tag.slug}?format=html")
        self.assertEqual(proxy_resp.status_code, 200)
        self._assert_preview_headers(proxy_resp)
        proxy_html = proxy_resp.content.decode("utf-8")
        self.assertIn("<p>Original body</p>", proxy_html)
        self.assertIn(
            f'<a class="pill" href="/rss/tag/proxy/{self.tag.slug}">Atom XML</a>',
            proxy_html,
        )
        self.assertNotIn(f"/rss/tag/json/{self.tag.slug}", proxy_html)

    def test_chinese_tag_preview(self):
        encoded = quote(self.chinese_tag.slug)
        resp = self.client.get(f"/rss/tag/{encoded}?format=html")
        self.assertEqual(resp.status_code, 200)
        self._assert_preview_headers(resp)
        html = resp.content.decode("utf-8")
        self.assertIn(f'<a class="pill" href="/rss/tag/{encoded}">Atom XML</a>', html)
        self.assertIn(f'href="/rss/tag/json/{encoded}"', html)

    def test_digest_preview_admin_link_and_public_view_intact(self):
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

        # Bare digest URL always returns XML
        xml_resp = self.client.get(f"/rss/digest/{digest.slug}", **_BROWSER_HEADERS)
        self.assertEqual(xml_resp.status_code, 200)
        self.assertEqual(xml_resp["Content-Type"], "application/xml; charset=utf-8")
        digest_etag = xml_resp["ETag"]

        # Explicit ?format=html renders preview with its own -html ETag
        html_resp = self.client.get(
            f"/rss/digest/{digest.slug}?format=html",
            HTTP_IF_NONE_MATCH=digest_etag,
        )
        self.assertEqual(html_resp.status_code, 200)
        self._assert_preview_headers(html_resp)
        self.assertTrue(html_resp["ETag"].strip('"').endswith("-html"))
        html = html_resp.content.decode("utf-8")
        self.assertIn("Daily Briefing", html)
        self.assertIn("Important news", html)
        self.assertIn(
            f'<a class="pill" href="/rss/digest/{digest.slug}">Atom XML</a>', html
        )
        self.assertIn(f'href="/rss/digest/json/{digest.slug}"', html)
        self.assertIn(
            f'<code class="feed-path">http://testserver/rss/digest/{digest.slug}</code>',
            html,
        )
        # Digest's feed.link points back to /rss/digest/<slug> itself, so the
        # redundant self-referencing Website button must be omitted.
        self.assertNotIn(">Website</a>", html)

        # DigestAdmin.show_url exposes rss | json | preview
        digest_admin = DigestAdmin(Digest, AdminSite())
        admin_links = digest_admin.show_url(digest)
        self.assertIn(f"/rss/digest/{digest.slug}", admin_links)
        self.assertIn(f"/rss/digest/json/{digest.slug}", admin_links)
        self.assertIn(f"/rss/digest/{digest.slug}?format=html", admin_links)
        self.assertIn("preview", admin_links)

        # Existing /rss/digest/view/<slug> continues to work unchanged
        view_resp = self.client.get(f"/rss/digest/view/{digest.slug}")
        self.assertEqual(view_resp.status_code, 200)
        self.assertIn("Important news", view_resp.content.decode("utf-8"))

    def test_make_response_html_empty_returns_404(self):
        from core.views import _make_response

        response = _make_response(None, "empty", "html")
        self.assertEqual(response.status_code, 404)

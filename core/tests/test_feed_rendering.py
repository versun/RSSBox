import json

from django.test import TestCase
from django.utils import timezone

from core.models import Feed, Entry, Tag


class FeedRenderingTests(TestCase):
    def setUp(self):
        self.tag = Tag.objects.create(name="render-tag")
        self.feed = Feed.objects.create(
            name="Render Feed",
            feed_url="https://example.com/render.xml",
            slug="render-feed",
        )
        self.feed.tags.add(self.tag)
        Entry.objects.create(
            feed=self.feed,
            link="https://example.com/article",
            guid="render-guid",
            original_title="Original Title",
            translated_title="翻译标题",
            original_content="<p>Original Content</p>",
            translated_content="<p>翻译内容</p>",
            ai_summary="Summary",
            pubdate=timezone.now(),
        )

    def test_render_feed_content_original_and_translated(self):
        from core.services.feed import render_feed_content

        original = render_feed_content(self.feed, feed_type="o")
        translated = render_feed_content(self.feed, feed_type="t")

        self.assertIn("Original Title", original)
        self.assertNotIn("翻译标题", original)
        self.assertIn("翻译标题", translated)
        self.assertIn("Summary", translated)

    def test_render_tag_content_merges_multiple_feeds(self):
        from core.services.feed import render_tag_content

        second_feed = Feed.objects.create(
            name="Render Feed 2",
            feed_url="https://example.com/render2.xml",
            slug="render-feed-2",
        )
        second_feed.tags.add(self.tag)
        Entry.objects.create(
            feed=second_feed,
            link="https://example.com/article2",
            guid="render-guid-2",
            original_title="Second Title",
            original_content="<p>Second Content</p>",
            pubdate=timezone.now(),
        )

        merged = render_tag_content(self.tag.slug, Feed.objects.filter(tags=self.tag))

        self.assertIn("翻译标题", merged)
        self.assertIn("Second Title", merged)


class FeedResponseTests(TestCase):
    def test_build_feed_response_returns_json(self):
        from core.services.feed import build_feed_response

        response = build_feed_response(
            "<rss><channel><title>JSON Feed</title></channel></rss>",
            "json-feed",
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)["title"], "JSON Feed")

    def test_build_feed_response_returns_xml_stream(self):
        from core.services.feed import build_feed_response

        response = build_feed_response(
            "<rss><channel><title>XML Feed</title></channel></rss>",
            "xml-feed",
            format="xml",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/xml; charset=utf-8")
        self.assertEqual(
            b"".join(response.streaming_content),
            b"<rss><channel><title>XML Feed</title></channel></rss>",
        )

    def test_build_feed_response_returns_404_json_when_empty(self):
        from core.services.feed import build_feed_response

        response = build_feed_response(None, "missing-feed", format="json")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(json.loads(response.content)["error"], "No feed data available")

    def test_build_feed_response_returns_error_xml_when_empty(self):
        from core.services.feed import build_feed_response

        response = build_feed_response(None, "missing-feed", format="xml")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            b"".join(response.streaming_content),
            b"<error>No feed data available</error>",
        )

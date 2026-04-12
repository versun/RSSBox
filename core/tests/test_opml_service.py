from django.test import TestCase

from core.models import Feed, Tag


class OpmlServiceTests(TestCase):
    def test_import_opml_content_creates_feed(self):
        from core.services.opml import import_opml_content

        created_count = import_opml_content(
            b"""
            <opml version=\"2.0\">
                <body>
                    <outline text=\"Feed 1\" title=\"Feed 1\" type=\"rss\" xmlUrl=\"http://example.com/feed1.xml\" />
                </body>
            </opml>
            """
        )

        self.assertEqual(created_count, 1)
        self.assertTrue(Feed.objects.filter(feed_url="http://example.com/feed1.xml").exists())

    def test_import_opml_content_creates_nested_tags(self):
        from core.services.opml import import_opml_content

        created_count = import_opml_content(
            b"""
            <opml version=\"2.0\">
                <body>
                    <outline text=\"News\">
                        <outline text=\"Tech News\" title=\"Tech News\" type=\"rss\" xmlUrl=\"http://example.com/technews.xml\" />
                    </outline>
                </body>
            </opml>
            """
        )

        self.assertEqual(created_count, 1)
        feed = Feed.objects.get(feed_url="http://example.com/technews.xml")
        self.assertTrue(feed.tags.filter(name="News").exists())

    def test_build_opml_response_groups_feeds_by_tag(self):
        from core.services.opml import build_opml_response

        tag = Tag.objects.create(name="Tech")
        feed = Feed.objects.create(name="Feed", feed_url="http://example.com/feed.xml")
        feed.tags.add(tag)

        response = build_opml_response(
            title_prefix="Test Export",
            queryset=Feed.objects.filter(id=feed.id),
            get_feed_url_func=lambda current_feed: current_feed.feed_url,
            filename_prefix="test",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("test_feeds_from_rssbox.opml", response["Content-Disposition"])
        self.assertIn(b"Test Export | RSSBox", response.content)

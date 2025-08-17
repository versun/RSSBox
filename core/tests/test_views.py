from django.test import TestCase, RequestFactory
from django.http import Http404, JsonResponse
from unittest.mock import patch, MagicMock
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.urls import reverse
from django.contrib.messages.storage.fallback import FallbackStorage
import io
import json

from ..models import Feed, Tag
from ..views import rss, tag as tag_view, import_opml


class ViewsTestCase(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.feed = Feed.objects.create(
            name="Test Feed", 
            feed_url="https://example.com/rss.xml", 
            slug="test-feed"
        )
        self.tag = Tag.objects.create(name="Test Tag", slug="test-tag")

    def _create_opml_file(self, content, filename="test.opml"):
        """Helper method to create OPML file for testing."""
        return InMemoryUploadedFile(
            file=io.BytesIO(content.encode("utf-8")),
            field_name="opml_file",
            name=filename,
            content_type="application/xml",
            size=len(content),
            charset="utf-8",
        )

    def _setup_request_with_messages(self, request):
        """Helper method to setup request with messages."""
        setattr(request, "session", "session")
        messages = FallbackStorage(request)
        setattr(request, "_messages", messages)
        return messages

    @patch('core.views.cache')
    @patch('core.views.cache_rss')
    def test_rss_feed_view_found(self, mock_cache_rss, mock_cache):
        """Test the rss view when the feed is found."""
        mock_cache.get.return_value = None  # Cache miss
        mock_cache_rss.return_value = "<rss><channel><title>Test Feed</title></channel></rss>"
        
        request = self.factory.get(f"/rss/{self.feed.slug}")
        response = rss(request, self.feed.slug)
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/xml; charset=utf-8")
        mock_cache_rss.assert_called_once_with(self.feed.slug, "t", "xml")

    def test_rss_feed_view_not_found(self):
        """Test the rss view when the feed is not found."""
        request = self.factory.get("/rss/non-existent-slug")
        response = rss(request, "non-existent-slug")
        self.assertEqual(response.status_code, 404)

    @patch('core.views.cache')
    @patch('core.views.cache_tag')
    def test_tag_view_found(self, mock_cache_tag, mock_cache):
        """Test the tag view when the tag is found."""
        mock_cache.get.return_value = None  # Cache miss
        mock_cache_tag.return_value = "<rss><channel><title>Tag Feed</title></channel></rss>"
        
        request = self.factory.get(f"/tag/{self.tag.slug}")
        response = tag_view(request, self.tag.slug)
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/xml; charset=utf-8")
        mock_cache_tag.assert_called_once_with(self.tag.slug, "t", "xml")

    def test_tag_view_not_found(self):
        """Test the tag view when the tag is not found."""
        request = self.factory.get("/tag/non-existent-tag")
        response = tag_view(request, "non-existent-tag")
        self.assertEqual(response.status_code, 404)

    def test_import_opml_success(self):
        """Test the import_opml view with a valid OPML file."""
        opml_content = """
        <opml version="2.0">
            <body>
                <outline text="Feed 1" title="Feed 1" type="rss" xmlUrl="http://example.com/feed1.xml" />
            </body>
        </opml>
        """
        opml_file = self._create_opml_file(opml_content, "feeds.opml")
        request = self.factory.post("/fake-url", {"opml_file": opml_file})
        messages = self._setup_request_with_messages(request)

        initial_feed_count = Feed.objects.count()
        response = import_opml(request)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("admin:core_feed_changelist"))
        self.assertEqual(Feed.objects.count(), initial_feed_count + 1)
        self.assertTrue(
            Feed.objects.filter(feed_url="http://example.com/feed1.xml").exists()
        )
        self.assertIn("OPML file imported successfully.", [str(m) for m in messages])

    def test_import_opml_with_nested_categories(self):
        """Test importing an OPML file with nested categories."""
        opml_content = """
        <opml version="2.0">
            <body>
                <outline text="News">
                    <outline text="Tech News" title="Tech News" type="rss" xmlUrl="http://example.com/technews.xml" />
                </outline>
            </body>
        </opml>
        """
        opml_file = self._create_opml_file(opml_content, "nested.opml")
        request = self.factory.post("/fake-url", {"opml_file": opml_file})
        self._setup_request_with_messages(request)

        import_opml(request)

        self.assertTrue(
            Feed.objects.filter(feed_url="http://example.com/technews.xml").exists()
        )
        new_feed = Feed.objects.get(feed_url="http://example.com/technews.xml")
        self.assertTrue(new_feed.tags.filter(name="News").exists())

    def test_import_opml_invalid_file(self):
        """Test importing an invalid OPML file (missing body)."""
        opml_content = "<opml version='2.0'><head></head></opml>"
        opml_file = self._create_opml_file(opml_content, "invalid.opml")
        request = self.factory.post("/fake-url", {"opml_file": opml_file})
        messages = self._setup_request_with_messages(request)

        initial_feed_count = Feed.objects.count()
        import_opml(request)

        self.assertEqual(Feed.objects.count(), initial_feed_count)
        self.assertIn("Invalid OPML: Missing body element", [str(m) for m in messages])

    @patch('core.views.feed2json')
    @patch('core.views.cache')
    @patch('core.views.cache_rss')
    def test_rss_view_json_format(self, mock_cache_rss, mock_cache, mock_feed2json):
        """Test the rss view with format='json'."""
        mock_cache.get.return_value = None  # Cache miss
        mock_cache_rss.return_value = "<rss><channel><title>Test Feed</title></channel></rss>"
        mock_feed2json.return_value = {"title": "JSON Feed"}

        request = self.factory.get(f"/rss/{self.feed.slug}")
        response = rss(request, self.feed.slug, feed_type="o", format="json")

        mock_cache_rss.assert_called_once_with(self.feed.slug, "o", "json")
        mock_feed2json.assert_called_once_with("<rss><channel><title>Test Feed</title></channel></rss>")
        self.assertIsInstance(response, JsonResponse)
        self.assertEqual(response.status_code, 200)
        json_content = json.loads(response.content)
        self.assertEqual(json_content["title"], "JSON Feed")

from django.test import TestCase, RequestFactory
from django.http import Http404, JsonResponse
from unittest.mock import patch
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
        self.feed = Feed.objects.create(name="Test Feed", feed_url="https://example.com/rss.xml", slug="test-feed")

    def test_rss_feed_view_found(self):
        """Test the rss_feed view when the feed is found."""
        request = self.factory.get(f'/rss/{self.feed.slug}')
        response = rss(request, self.feed.slug)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/xml; charset=utf-8')

    def test_rss_feed_view_not_found(self):
        """Test the rss_feed view when the feed is not found."""
        request = self.factory.get('/rss/non-existent-slug')
        response = rss(request, 'non-existent-slug')
        self.assertEqual(response.status_code, 404)

    def test_tag_view_found(self):
        """Test the tag view when the tag is found."""
        tag_obj = Tag.objects.create(name="Test Tag", slug="test-tag")
        request = self.factory.get(f'/tag/{tag_obj.slug}')
        response = tag_view(request, tag_obj.slug)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/xml; charset=utf-8')

    def test_tag_view_not_found(self):
        """Test the tag view when the tag is not found."""
        request = self.factory.get('/tag/non-existent-tag')
        response = tag_view(request, 'non-existent-tag')
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
        opml_file = InMemoryUploadedFile(
            file=io.BytesIO(opml_content.encode('utf-8')),
            field_name='opml_file',
            name='feeds.opml',
            content_type='application/xml',
            size=len(opml_content),
            charset='utf-8'
        )
        request = self.factory.post('/fake-url', {'opml_file': opml_file})
        setattr(request, 'session', 'session')
        messages = FallbackStorage(request)
        setattr(request, '_messages', messages)

        # Initial feed count from setUp
        initial_feed_count = Feed.objects.count()

        response = import_opml(request)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('admin:core_feed_changelist'))
        self.assertEqual(Feed.objects.count(), initial_feed_count + 1)
        self.assertTrue(Feed.objects.filter(feed_url="http://example.com/feed1.xml").exists())

        message_texts = [str(m) for m in messages]
        self.assertIn("OPML file imported successfully.", message_texts)

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
        opml_file = InMemoryUploadedFile(
            file=io.BytesIO(opml_content.encode('utf-8')),
            field_name='opml_file',
            name='nested.opml',
            content_type='application/xml',
            size=len(opml_content),
            charset='utf-8'
        )
        request = self.factory.post('/fake-url', {'opml_file': opml_file})
        setattr(request, 'session', 'session')
        messages = FallbackStorage(request)
        setattr(request, '_messages', messages)

        import_opml(request)

        self.assertTrue(Feed.objects.filter(feed_url="http://example.com/technews.xml").exists())
        new_feed = Feed.objects.get(feed_url="http://example.com/technews.xml")
        self.assertTrue(new_feed.tags.filter(name="News").exists())

    def test_import_opml_invalid_file(self):
        """Test importing an invalid OPML file (missing body)."""
        opml_content = "<opml version='2.0'><head></head></opml>"
        opml_file = InMemoryUploadedFile(
            file=io.BytesIO(opml_content.encode('utf-8')),
            field_name='opml_file',
            name='invalid.opml',
            content_type='application/xml',
            size=len(opml_content),
            charset='utf-8'
        )
        request = self.factory.post('/fake-url', {'opml_file': opml_file})
        setattr(request, 'session', 'session')
        messages = FallbackStorage(request)
        setattr(request, '_messages', messages)

        initial_feed_count = Feed.objects.count()
        import_opml(request)

        self.assertEqual(Feed.objects.count(), initial_feed_count)
        message_texts = [str(m) for m in messages]
        self.assertIn("Invalid OPML: Missing body element", message_texts)

    @patch('core.views.feed2json')
    @patch('core.views.cache')
    def test_rss_view_json_format_and_original_type(self, mock_cache, mock_feed2json):
        """Test the rss view with format='json' and feed_type='o'."""
        mock_cache.get.return_value = None  # Simulate cache miss
        mock_cache.set.return_value = None
        mock_feed2json.return_value = {'title': 'JSON Feed'}

        with patch('core.views.cache_rss', return_value="<rss></rss>") as mock_cache_rss:
            request = self.factory.get(f'/rss/{self.feed.slug}')
            response = rss(request, self.feed.slug, feed_type='o', format='json')

            mock_cache_rss.assert_called_once_with(self.feed.slug, 'o', 'json')
            mock_feed2json.assert_called_once_with("<rss></rss>")
            self.assertIsInstance(response, JsonResponse)
            self.assertEqual(response.status_code, 200)
            json_content = json.loads(response.content)
            self.assertEqual(json_content['title'], 'JSON Feed')

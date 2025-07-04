from django.test import TestCase
from .models import Feed, Entry
from django.utils import timezone
from django.test import Client
from unittest.mock import MagicMock, patch
from django.core.cache import cache
from . import cache as cache_module
from .tasks import handle_single_feed_fetch, handle_feeds_fetch

class FeedModelTest(TestCase):
    def test_create_feed(self):
        feed = Feed.objects.create(
            name="Test Feed",
            feed_url="https://example.com/feed.xml",
            target_language="en",
        )
        self.assertEqual(feed.name, "Test Feed")
        self.assertEqual(feed.feed_url, "https://example.com/feed.xml")
        self.assertEqual(feed.target_language, "en")
        self.assertIsNotNone(feed.slug)

    def test_feed_str(self):
        feed = Feed.objects.create(
            name="Feed2",
            feed_url="https://example.com/2.xml",
            target_language="en",
        )
        self.assertEqual(str(feed), feed.feed_url)

class EntryModelTest(TestCase):
    def setUp(self):
        self.feed = Feed.objects.create(
            name="Test Feed",
            feed_url="https://example.com/feed.xml",
            target_language="en",
        )

    def test_create_entry(self):
        entry = Entry.objects.create(
            feed=self.feed,
            link="https://example.com/post1",
            original_title="Original Title",
            translated_title="Translated Title",
            pubdate=timezone.now(),
        )
        self.assertEqual(entry.feed, self.feed)
        self.assertEqual(entry.link, "https://example.com/post1")
        self.assertEqual(entry.original_title, "Original Title")
        self.assertEqual(entry.translated_title, "Translated Title")

    def test_entry_str(self):
        entry = Entry.objects.create(
            feed=self.feed,
            link="https://example.com/post2",
            original_title="Title2",
            pubdate=timezone.now(),
        )
        self.assertEqual(str(entry), "Title2")

class RSSViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.feed = Feed.objects.create(
            name="Test Feed",
            feed_url="https://example.com/feed.xml",
            target_language="en",
        )

    def test_rss_view_200(self):
        url = f"/rss/{self.feed.slug}"
        response = self.client.get(url, follow=True)
        self.assertIn(response.status_code, [200, 404])  # 200:正常, 404:无缓存/无内容

    def test_rss_view_404(self):
        url = "/rss/not-exist-feed-slug"
        response = self.client.get(url, follow=True)
        self.assertEqual(response.status_code, 404)

class CategoryViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.feed = Feed.objects.create(
            name="Test Feed",
            feed_url="https://example.com/feed.xml",
            target_language="en",
            category="testcat"
        )

    def test_category_view_200_or_404(self):
        url = "/rss/category/testcat"
        response = self.client.get(url, follow=True)
        self.assertIn(response.status_code, [200, 404])

    def test_category_view_404(self):
        url = "/rss/category/notexistcat"
        response = self.client.get(url, follow=True)
        self.assertEqual(response.status_code, 404)

class AdminSiteTest(TestCase):
    def setUp(self):
        from core.custom_admin_site import core_admin_site
        from .models import Feed, Entry
        self.site = core_admin_site
        self.feed = Feed
        self.entry = Entry
        self.client = Client()

    def test_feed_registered(self):
        # 检查 Feed 是否注册到自定义 admin
        self.assertIn(self.feed, self.site._registry)

    def test_custom_admin_urls(self):
        # 测试自定义 admin url 是否可访问（未登录会重定向）
        response_list = self.client.get('/admin/translator/list', follow=True)
        response_add = self.client.get('/admin/translator/add', follow=True)
        self.assertIn(response_list.status_code, [200, 302, 403])
        self.assertIn(response_add.status_code, [200, 302, 403])

    def test_site_titles(self):
        # 测试自定义 admin 的站点标题属性
        self.assertEqual(str(self.site.site_header), 'RSS Translator Admin')
        self.assertEqual(str(self.site.site_title), 'RSS Translator')
        self.assertEqual(str(self.site.index_title), 'Dashboard')

class CacheFunctionTest(TestCase):
    def setUp(self):
        self.feed = Feed.objects.create(
            name="Test Feed",
            feed_url="https://example.com/feed.xml",
            target_language="en",
            category="testcat"
        )
        self.slug = self.feed.slug or "test-feed-slug"
        if not self.feed.slug:
            self.feed.slug = self.slug
            self.feed.save()
        cache.clear()

    @patch("core.cache.generate_atom_feed")
    def test_cache_rss_success(self, mock_generate_atom_feed):
        mock_generate_atom_feed.return_value = "<feed>atom</feed>"
        result = cache_module.cache_rss(self.feed.slug or self.slug)
        cache_key = f"cache_rss_{self.feed.slug or self.slug}_t_xml"
        self.assertEqual(result, "<feed>atom</feed>")
        self.assertEqual(cache.get(cache_key), "<feed>atom</feed>")
        mock_generate_atom_feed.assert_called_once()

    @patch("core.cache.generate_atom_feed")
    def test_cache_rss_none(self, mock_generate_atom_feed):
        mock_generate_atom_feed.return_value = None
        result = cache_module.cache_rss(self.feed.slug or self.slug)
        self.assertIsNone(result)

    @patch("core.cache.merge_feeds_into_one_atom")
    def test_cache_category_success(self, mock_merge):
        mock_merge.return_value = "<feed>merged</feed>"
        result = cache_module.cache_category("testcat")
        cache_key = "cache_category_testcat_t_xml"
        self.assertEqual(result, "<feed>merged</feed>")
        self.assertEqual(cache.get(cache_key), "<feed>merged</feed>")
        mock_merge.assert_called_once()

    @patch("core.cache.merge_feeds_into_one_atom")
    def test_cache_category_none(self, mock_merge):
        mock_merge.return_value = None
        result = cache_module.cache_category("testcat")
        self.assertIsNone(result)



class TasksTestCase(TestCase):
    def setUp(self):
        self.feed = Feed.objects.create(
            name="Test Feed",
            feed_url="https://example.com/feed.xml",
            target_language="en",
        )

    @patch("core.tasks.fetch_feed")
    @patch("core.tasks.convert_struct_time_to_datetime", return_value=timezone.now())
    def test_handle_single_feed_fetch_success(self, mock_convert, mock_fetch_feed):
        mock_fetch_feed.return_value = {
            "error": None,
            "update": True,
            "feed": MagicMock(
                feed={
                    "title": "Feed Title",
                    "subtitle": "Sub",
                    "language": "en",
                    "author": "Author",
                    "link": "https://example.com/feed.xml",
                    "published_parsed": None,
                    "updated_parsed": None,
                },
                entries=[{
                    "id": "guid1",
                    "link": "https://example.com/post1",
                    "author": "Author",
                    "title": "Title1",
                    "summary": "Summary1",
                    "enclosures_xml": None,
                    "published_parsed": None,
                    "updated_parsed": None,
                }],
                get=lambda k, default=None: None if k == "etag" else mock_fetch_feed.return_value["feed"].feed.get(k, default)
            )
        }
        self.feed.max_posts = 5
        handle_single_feed_fetch(self.feed)
        self.feed.refresh_from_db()
        self.assertTrue(self.feed.fetch_status)
        self.assertIn("Fetch Completed", self.feed.log or "")
        self.assertEqual(Entry.objects.filter(feed=self.feed).count(), 1)

    @patch("core.tasks.fetch_feed")
    def test_handle_single_feed_fetch_error(self, mock_fetch_feed):
        mock_fetch_feed.return_value = {"error": "Network error", "update": False, "feed": None}
        handle_single_feed_fetch(self.feed)
        self.feed.refresh_from_db()
        self.assertFalse(self.feed.fetch_status)
        self.assertIn("Network error", self.feed.log or "")

    @patch("core.tasks.handle_single_feed_fetch")
    def test_handle_feeds_fetch(self, mock_handle_single):
        feed2 = Feed.objects.create(name="Feed2", feed_url="https://example.com/2.xml", target_language="en")
        feeds = [self.feed, feed2]
        handle_feeds_fetch(feeds)
        self.assertEqual(mock_handle_single.call_count, 2)
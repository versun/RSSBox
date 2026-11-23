from datetime import timedelta
from importlib import import_module
from unittest.mock import patch
from urllib.parse import urlsplit

from django.apps import apps
from django.conf import settings
from django.core.cache import cache
from django.db import OperationalError
from django.test import TestCase
from django.urls import resolve
from django.utils import timezone

from core import views
from core.models import Digest, Entry, Feed, OpenAIAgent, Tag
from core.models.filter import cache_version
from core.tasks.generate_digests import DigestGenerator


class DigestEntryLinkTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.agent = OpenAIAgent.objects.create(
            name="Digest summarizer", api_key="unused", max_tokens=4096
        )
        self.feed = Feed.objects.create(
            name="News feed",
            slug="news-feed",
            feed_url="https://example.com/news.xml",
            summarizer=self.agent,
        )
        self.tag = Tag.objects.create(name="Daily news")
        self.feed.tags.add(self.tag)
        Entry.objects.create(
            feed=self.feed,
            link="https://example.com/article",
            original_title="Important article",
            original_content="Article body",
            ai_summary="Article summary",
            pubdate=timezone.now() - timedelta(hours=1),
        )
        self.digest = Digest.objects.create(
            name="Daily digest",
            summarizer=self.agent,
            target_language="English",
            days_range=1,
        )
        self.digest.tags.add(self.tag)

    def test_digest_entry_links_to_public_digest_page(self):
        with patch.object(
            OpenAIAgent,
            "digester",
            return_value={"text": "Digest body for readers", "tokens": 1},
        ):
            result = DigestGenerator(self.digest).generate()

        self.assertTrue(result["success"], result)
        entry = Entry.objects.get(pk=result["entry_id"])
        path = urlsplit(entry.link).path

        # The link must open the public digest page, not an admin URL that
        # sends anonymous readers (and feed reader scrapers) to the login form.
        self.assertEqual(resolve(path).func, views.digest_view)
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Digest body for readers")

    def _generate(self, text):
        with patch.object(
            OpenAIAgent, "digester", return_value={"text": text, "tokens": 1}
        ):
            result = DigestGenerator(self.digest).generate()
        self.assertTrue(result["success"], result)
        return Entry.objects.get(pk=result["entry_id"])

    def test_each_digest_entry_links_to_its_own_digest(self):
        # Readers that scrape the entry link must get that entry's digest,
        # not the newest one (all entries used to share one link).
        first = self._generate("First digest body")
        self.digest.refresh_from_db()
        self.digest.last_generated = None
        self.digest.save()
        second = self._generate("Second digest body")

        self.assertNotEqual(first.link, second.link)
        first_page = self.client.get(urlsplit(first.link).path)
        self.assertContains(first_page, "First digest body")
        self.assertNotContains(first_page, "Second digest body")
        second_page = self.client.get(urlsplit(second.link).path)
        self.assertContains(second_page, "Second digest body")

        # The slug-only page still shows the newest digest.
        latest = self.client.get(f"/rss/digest/view/{self.digest.slug}")
        self.assertContains(latest, "Second digest body")

    def test_entry_of_another_digest_is_not_served(self):
        entry = self._generate("Digest body")
        other = Digest.objects.create(
            name="Other digest", summarizer=self.agent, target_language="English"
        )
        response = self.client.get(f"/rss/digest/view/{other.slug}/{entry.pk}")
        self.assertEqual(response.status_code, 404)

    def test_page_shows_the_date_of_the_digest_it_renders(self):
        first = self._generate("First digest body")
        first.pubdate = (timezone.now() - timedelta(days=2)).replace(microsecond=0)
        first.save(update_fields=["pubdate"])
        self.digest.refresh_from_db()
        self.digest.last_generated = None
        self.digest.save()
        second = self._generate("Second digest body")

        def generated_line(entry):
            stamp = timezone.localtime(entry.pubdate).strftime("%Y-%m-%d %H:%M:%S")
            return f"<strong>Generated:</strong> {stamp}<br>"

        self.assertNotEqual(generated_line(first), generated_line(second))
        first_page = self.client.get(urlsplit(first.link).path)
        self.assertContains(first_page, generated_line(first))
        slug_page = self.client.get(f"/rss/digest/view/{self.digest.slug}")
        self.assertContains(slug_page, generated_line(second))

        # With the newest entry gone, the slug-only page falls back to the
        # older digest and must show that digest's date, not last_generated.
        second.delete()
        slug_page = self.client.get(f"/rss/digest/view/{self.digest.slug}")
        self.assertContains(slug_page, "First digest body")
        self.assertContains(slug_page, generated_line(first))

    def test_failed_link_update_leaves_no_entry_behind(self):
        original_save = Entry.save

        def fail_on_link_update(entry, *args, **kwargs):
            if kwargs.get("update_fields") == ["link"]:
                raise OperationalError("database is locked")
            return original_save(entry, *args, **kwargs)

        with (
            patch.object(Entry, "save", fail_on_link_update),
            patch.object(
                OpenAIAgent,
                "digester",
                return_value={"text": "Digest body", "tokens": 1},
            ),
        ):
            result = DigestGenerator(self.digest).generate()

        self.assertFalse(result["success"])
        self.assertFalse(self.digest.get_digest_feed().entries.exists())


class DigestEntryPermalinkMigrationTests(TestCase):
    """Data migration 0046: existing digest entries get their own link."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.migration = import_module("core.migrations.0046_digest_entry_permalinks")
        self.site = settings.SITE_URL.rstrip("/")
        agent = OpenAIAgent.objects.create(
            name="Digest summarizer", api_key="unused", max_tokens=4096
        )
        self.digest = Digest.objects.create(
            name="Daily digest",
            slug="daily",
            summarizer=agent,
            target_language="English",
        )
        self.digest_feed = self.digest.get_digest_feed()
        self.old_link = f"{self.site}/rss/digest/view/daily"

    def _entry(self, feed, link):
        return Entry.objects.create(
            feed=feed,
            link=link,
            original_title="Digest",
            ai_summary="Digest body",
            pubdate=timezone.now(),
        )

    def _migrate(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.migration.add_entry_ids(apps, None)

    def test_local_digest_entries_get_their_own_link(self):
        plain = self._entry(self.digest_feed, self.old_link)
        with_slash = self._entry(self.digest_feed, f"{self.old_link}/")

        self._migrate()

        plain.refresh_from_db()
        with_slash.refresh_from_db()
        self.assertEqual(plain.link, f"{self.old_link}/{plain.pk}")
        self.assertEqual(with_slash.link, f"{self.old_link}/{with_slash.pk}")
        response = self.client.get(urlsplit(plain.link).path)
        self.assertEqual(response.status_code, 200)

    def test_running_twice_changes_nothing_more(self):
        entry = self._entry(self.digest_feed, self.old_link)

        self._migrate()
        version = cache_version("digest", self.digest.pk)
        self._migrate()

        entry.refresh_from_db()
        self.assertEqual(entry.link, f"{self.old_link}/{entry.pk}")
        self.assertEqual(cache_version("digest", self.digest.pk), version)

    def test_entries_of_other_feeds_are_left_alone(self):
        # A subscribed digest of another RSSBox instance with the same slug.
        external_feed = Feed.objects.create(
            name="Friend's digest",
            feed_url="https://friend.example/rss/digest/daily",
        )
        external_link = "https://friend.example/rss/digest/view/daily"
        external = self._entry(external_feed, external_link)
        # An ordinary article whose link happens to end the same way.
        news_feed = Feed.objects.create(
            name="News", feed_url="https://example.com/news.xml"
        )
        article_link = "https://example.com/mirror/rss/digest/view/daily"
        article = self._entry(news_feed, article_link)
        version = cache_version("digest", self.digest.pk)

        self._migrate()

        external.refresh_from_db()
        article.refresh_from_db()
        self.assertEqual(external.link, external_link)
        self.assertEqual(article.link, article_link)
        # Nothing changed for the local digest, so its cache stays valid.
        self.assertEqual(cache_version("digest", self.digest.pk), version)

    def test_cache_version_is_bumped_for_rewritten_digests_only(self):
        other = Digest.objects.create(
            name="Weekly digest",
            slug="weekly",
            summarizer=self.digest.summarizer,
            target_language="English",
        )
        other.get_digest_feed()
        self._entry(self.digest_feed, self.old_link)
        version = cache_version("digest", self.digest.pk)
        other_version = cache_version("digest", other.pk)

        self._migrate()

        self.assertEqual(cache_version("digest", self.digest.pk), version + 1)
        self.assertEqual(cache_version("digest", other.pk), other_version)

from datetime import timedelta
from unittest.mock import patch
from urllib.parse import urlsplit

from django.test import TestCase
from django.urls import resolve
from django.utils import timezone

from core import views
from core.models import Digest, Entry, Feed, OpenAIAgent, Tag
from core.tasks.generate_digests import DigestGenerator


class DigestEntryLinkTests(TestCase):
    def setUp(self):
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

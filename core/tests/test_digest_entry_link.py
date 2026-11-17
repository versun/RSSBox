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

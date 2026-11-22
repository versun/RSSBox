from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from core.models import Digest, Entry, Feed, OpenAIAgent, Tag


class DigestWindowTests(TestCase):
    def setUp(self):
        agent = OpenAIAgent.objects.create(name="Agent", api_key="unused")
        self.feed = Feed.objects.create(
            name="News feed", slug="news-feed", feed_url="https://example.com/news.xml"
        )
        tag = Tag.objects.create(name="News")
        self.feed.tags.add(tag)
        self.digest = Digest.objects.create(
            name="Digest", summarizer=agent, target_language="English", days_range=3
        )
        self.digest.tags.add(tag)
        self.now = timezone.now()

    def _entry(self, title, age):
        return Entry.objects.create(
            feed=self.feed,
            link=f"https://example.com/{title}",
            original_title=title,
            pubdate=self.now - age,
        )

    def _titles(self):
        return set(
            self.digest.get_articles_for_digest().values_list("original_title", flat=True)
        )

    def test_without_a_previous_digest_days_range_sets_the_window(self):
        self._entry("recent", timedelta(days=1))
        self._entry("too-old", timedelta(days=6))
        self.assertEqual(self._titles(), {"recent"})

    def test_window_starts_at_the_last_digest(self):
        # Entries already covered by the previous digest are not repeated.
        self.digest.last_generated = self.now - timedelta(days=1)
        self._entry("covered-last-time", timedelta(days=2))
        self._entry("new-since", timedelta(hours=12))
        self.assertEqual(self._titles(), {"new-since"})

    def test_skipped_days_are_not_lost(self):
        # A Friday digest followed by a Monday one: the weekend's entries,
        # older than one day, still make it in.
        self.digest.days_range = 3
        self.digest.last_generated = self.now - timedelta(days=3)
        self._entry("saturday", timedelta(days=2))
        self._entry("sunday", timedelta(days=1))
        self.assertEqual(self._titles(), {"saturday", "sunday"})

    def test_days_range_still_caps_a_long_gap(self):
        self.digest.days_range = 2
        self.digest.last_generated = self.now - timedelta(days=10)
        self._entry("within-range", timedelta(days=1))
        self._entry("beyond-range", timedelta(days=5))
        self.assertEqual(self._titles(), {"within-range"})

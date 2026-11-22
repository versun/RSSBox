from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from core.models import Digest, Entry, Feed, OpenAIAgent, Tag
from core.tasks.generate_digests import DigestGenerator


class DigestPromptTests(TestCase):
    def setUp(self):
        self.agent = OpenAIAgent.objects.create(
            name="Digest summarizer", api_key="unused", max_tokens=100000
        )
        feed = Feed.objects.create(
            name="News feed",
            slug="news-feed",
            feed_url="https://example.com/news.xml",
            summarizer=self.agent,
        )
        tag = Tag.objects.create(name="Daily news")
        feed.tags.add(tag)
        Entry.objects.create(
            feed=feed,
            link="https://example.com/article",
            author="Jane Writer",
            original_title="Important article",
            ai_summary="Article summary",
            pubdate=timezone.now() - timedelta(hours=1),
        )
        self.digest = Digest.objects.create(
            name="Morning Brief",
            description="Things worth knowing",
            summarizer=self.agent,
            target_language="German",
            days_range=1,
            prompt=(
                "Write {digest_name} for {date} in {target_language}. "
                "Scope: {description}."
            ),
        )
        self.digest.tags.add(tag)

    def _build_prompt(self):
        generator = DigestGenerator(self.digest)
        generator.prepare_articles()
        return generator.build_prompt()

    def test_digest_prompt_placeholders_are_filled(self):
        # The prompt field's help text offers these placeholders; they used
        # to be replaced only in the appended output format.
        with patch(
            "django.utils.timezone.localdate", return_value=datetime(2026, 11, 20).date()
        ):
            _, system_prompt, _ = self._build_prompt()

        self.assertIn(
            "Write Morning Brief for 2026-11-20 in German. "
            "Scope: Things worth knowing.",
            system_prompt,
        )
        for placeholder in ("{digest_name}", "{date}", "{target_language}", "{description}"):
            self.assertNotIn(placeholder, system_prompt)

    def test_date_placeholder_uses_the_local_date(self):
        # Digests run in the early morning: at 01:00 in UTC+2 it is still
        # the previous day in UTC, and the digest must carry the local date.
        utc_now = datetime(2026, 11, 19, 23, 0, tzinfo=dt_timezone.utc)
        with self.settings(TIME_ZONE="Europe/Sofia"), patch(
            "django.utils.timezone.now", return_value=utc_now
        ):
            _, system_prompt, _ = self._build_prompt()
        self.assertIn("for 2026-11-20 in", system_prompt)

    def test_articles_carry_their_author(self):
        articles, _, _ = self._build_prompt()
        self.assertIn("Author: Jane Writer", "".join(articles))

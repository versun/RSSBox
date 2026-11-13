import json
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from core.models import Entry, Feed, Filter, FilterResult, OpenAIAgent


class AIFilterDegradationTests(TestCase):
    """AI filter failures must not hide entries or trigger a retry storm."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.agent = OpenAIAgent.objects.create(
            name="Filter agent", api_key="test", max_tokens=4096, valid=True
        )
        self.feed = Feed.objects.create(
            name="Filtered feed",
            slug="filtered-feed",
            feed_url="https://example.com/filtered.xml",
        )
        self.entry = Entry.objects.create(
            feed=self.feed,
            link="https://example.com/entry",
            original_title="An article",
            original_content="Article body",
            translated_content="Translated body",
            pubdate=timezone.now(),
        )
        self.rule = Filter.objects.create(
            name="AI rule",
            agent=self.agent,
            filter_method=Filter.AI_ONLY,
            filter_prompt="Is this relevant?",
        )

    def apply(self):
        return self.rule.apply_filter(Entry.objects.all())

    def test_failure_without_previous_decision_shows_entry(self):
        with patch.object(OpenAIAgent, "filter", side_effect=RuntimeError("down")):
            self.assertTrue(self.apply().exists())

        result = self.rule.results.get(entry=self.entry)
        self.assertIsNone(result.passed)

    def test_failure_preserves_previous_passed_decision(self):
        result = FilterResult.objects.create(
            filter=self.rule, entry=self.entry, passed=True
        )
        Entry.objects.filter(pk=self.entry.pk).update(
            updated=result.last_updated + timedelta(seconds=1)
        )

        with patch.object(OpenAIAgent, "filter", side_effect=RuntimeError("down")):
            self.assertTrue(self.apply().exists())

        result.refresh_from_db()
        self.assertTrue(result.passed)

    def test_failure_preserves_previous_blocked_decision(self):
        result = FilterResult.objects.create(
            filter=self.rule, entry=self.entry, passed=False
        )
        Entry.objects.filter(pk=self.entry.pk).update(
            updated=result.last_updated + timedelta(seconds=1)
        )

        with patch.object(OpenAIAgent, "filter", side_effect=RuntimeError("down")):
            self.assertFalse(self.apply().exists())

        result.refresh_from_db()
        self.assertFalse(result.passed)

    def test_failure_is_not_retried_on_every_request(self):
        with patch.object(OpenAIAgent, "filter", side_effect=RuntimeError("down")) as ai:
            self.assertTrue(self.apply().exists())
            self.assertTrue(self.apply().exists())
            self.assertTrue(self.apply().exists())

        self.assertEqual(ai.call_count, 1)
        self.assertIsNone(self.rule.results.get(entry=self.entry).passed)

    def test_clearing_results_allows_immediate_reevaluation(self):
        with patch.object(OpenAIAgent, "filter", side_effect=RuntimeError("down")):
            self.apply()
        self.rule.results.all().delete()

        with patch.object(
            OpenAIAgent, "filter", return_value={"passed": False, "tokens": 1}
        ) as ai:
            self.assertFalse(self.apply().exists())

        ai.assert_called_once()

    def test_failure_for_one_entry_does_not_drop_the_others(self):
        second = Entry.objects.create(
            feed=self.feed,
            link="https://example.com/second",
            original_title="Second article",
            original_content="Second body",
            pubdate=timezone.now(),
        )
        def evaluate(text, system_prompt):
            if "An article" in text:
                raise RuntimeError("down")
            return {"passed": True, "tokens": 3}

        with patch.object(OpenAIAgent, "filter", side_effect=evaluate):
            entries = list(self.apply())

        self.assertCountEqual(
            [entry.pk for entry in entries], [self.entry.pk, second.pk]
        )
        self.assertIsNone(self.rule.results.get(entry=self.entry).passed)
        self.assertTrue(self.rule.results.get(entry=second).passed)

    def test_missing_content_fields_do_not_break_the_filter(self):
        self.rule.filter_original_content = True
        self.rule.filter_translated_content = True
        self.rule.filter_translated_title = True
        self.rule.save()

        for field in ("original_content", "translated_content"):
            with self.subTest(field=field):
                cache.clear()
                self.rule.results.all().delete()
                Entry.objects.filter(pk=self.entry.pk).update(
                    original_content="Article body",
                    translated_content="Translated body",
                )
                Entry.objects.filter(pk=self.entry.pk).update(**{field: None})

                with patch.object(
                    OpenAIAgent, "filter", return_value={"passed": True, "tokens": 1}
                ) as ai:
                    self.assertTrue(self.apply().exists())

                payload = json.loads(ai.call_args.kwargs["text"])
                self.assertEqual(payload[field], "")
                self.assertTrue(self.rule.results.get(entry=self.entry).passed)
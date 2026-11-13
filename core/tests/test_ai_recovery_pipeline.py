from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch
from xml.etree import ElementTree

import httpx
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from openai import APITimeoutError

from core.management.commands.feed_updater import update_single_feed
from core.models import Entry, Feed, Filter, FilterResult, OpenAIAgent, Tag
from core.tasks.translate_feeds import handle_feeds_translation


class AIRecoveryPipelineTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.agent = OpenAIAgent.objects.create(
            name="Recovery", api_key="test", max_tokens=4096
        )
        self.feed = Feed.objects.create(
            name="Recovery",
            slug="recovery",
            feed_url="https://example.com/feed",
            translator=self.agent,
            summarizer=self.agent,
            translate_title=False,
            translate_content=False,
            summary=False,
        )
        self.entries = [
            Entry.objects.create(
                feed=self.feed,
                link=f"https://example.com/{name}",
                original_title=name,
                original_content=f"Content for {name}.",
                pubdate=timezone.now(),
            )
            for name in ("Pending", "Good")
        ]
        self.rule = Filter.objects.create(
            name="AI", filter_method=Filter.AI_ONLY, agent=self.agent
        )
        self.feed.filters.add(self.rule)
        self.tag = Tag.objects.create(name="Recovery", slug="recovery")
        self.tag.filters.add(self.rule)
        self.feed.tags.add(self.tag)

    def timeout(self):
        return APITimeoutError(request=httpx.Request("POST", "https://example.com"))

    def response(self):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop", message=SimpleNamespace(content="Passed")
                )
            ],
            usage=SimpleNamespace(total_tokens=3),
        )

    def atom_titles(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        content = b"".join(response.streaming_content)
        response.close()
        tree = ElementTree.fromstring(content)
        self.assertEqual(tree.tag, "{http://www.w3.org/2005/Atom}feed")
        return [
            item.text
            for item in tree.findall(
                "{http://www.w3.org/2005/Atom}entry/{http://www.w3.org/2005/Atom}title"
            )
        ]

    def test_filter_failure_keeps_feed_and_tag_valid_and_recovers(self):
        client = Mock()
        client.with_options.return_value = client
        request = client.chat.completions.create

        def complete(**kwargs):
            if "Pending" in kwargs["messages"][-1]["content"]:
                raise self.timeout()
            return self.response()

        with patch.object(OpenAIAgent, "_init", return_value=client):
            for path in ("/rss/recovery", "/rss/tag/recovery"):
                with self.subTest(path=path):
                    cache.clear()
                    self.rule.results.all().delete()
                    request.reset_mock(side_effect=True)
                    request.side_effect = complete
                    # A failed AI evaluation fails open: the undecided entry is
                    # still shown instead of being hidden.
                    self.assertCountEqual(
                        self.atom_titles(path), ["Pending", "Good"]
                    )
                    self.assertEqual(request.call_count, 2)
                    self.assertIsNone(
                        self.rule.results.get(entry=self.entries[0]).passed
                    )
                    self.assertTrue(self.rule.results.get(entry=self.entries[1]).passed)
                    cache.clear()
                    request.reset_mock(side_effect=True)
                    request.return_value = self.response()
                    self.assertCountEqual(self.atom_titles(path), ["Pending", "Good"])
                    request.assert_called_once()

    def test_all_filter_failures_produce_a_valid_feed_with_entries(self):
        with patch.object(OpenAIAgent, "filter", side_effect=self.timeout()):
            self.assertCountEqual(
                self.atom_titles("/rss/recovery"), ["Pending", "Good"]
            )
        self.assertEqual(self.rule.results.filter(passed__isnull=True).count(), 2)

    def test_failed_reevaluation_keeps_old_decision(self):
        result = FilterResult.objects.create(
            filter=self.rule, entry=self.entries[0], passed=True
        )
        Entry.objects.filter(pk=self.entries[0].pk).update(
            updated=result.last_updated + timedelta(seconds=1)
        )
        with patch.object(OpenAIAgent, "filter", side_effect=self.timeout()):
            self.assertTrue(
                self.rule.apply_filter(
                    Entry.objects.filter(pk=self.entries[0].pk)
                ).exists()
            )
        result.refresh_from_db()
        self.assertTrue(result.passed)

    @patch("core.management.commands.feed_updater.close_old_connections")
    @patch("core.management.commands.feed_updater.handle_single_feed_fetch")
    @patch("core.tasks.utils.time.sleep")
    def test_update_aggregates_stage_failures_and_recovers_next_run(self, *_):
        self.feed.translate_title = self.feed.translate_content = self.feed.summary = (
            True
        )
        self.feed.save()
        for failed in ("title", "content", "summary"):
            with self.subTest(failed=failed):
                self.feed.entries.update(
                    translated_title=None, translated_content=None, ai_summary=None
                )

                def translate(failed=failed, **kwargs):
                    if kwargs["text_type"] == failed:
                        raise self.timeout()
                    return {"text": "Translated", "tokens": 3, "characters": 0}

                def summarize(failed=failed, **kwargs):
                    if failed == "summary":
                        raise self.timeout()
                    return {"text": "Summary", "tokens": 3}

                with (
                    patch.object(OpenAIAgent, "translate", side_effect=translate),
                    patch.object(OpenAIAgent, "summarize", side_effect=summarize),
                ):
                    self.assertFalse(update_single_feed(self.feed))
                self.feed.refresh_from_db()
                self.assertFalse(self.feed.translation_status)
                with (
                    patch.object(
                        OpenAIAgent,
                        "translate",
                        return_value={
                            "text": "Translated",
                            "tokens": 3,
                            "characters": 0,
                        },
                    ),
                    patch.object(
                        OpenAIAgent,
                        "summarize",
                        return_value={"text": "Summary", "tokens": 3},
                    ),
                ):
                    self.assertTrue(update_single_feed(self.feed))
                self.feed.refresh_from_db()
                self.assertTrue(self.feed.translation_status)

    def test_blank_titles_do_not_call_provider_or_retry(self):
        self.feed.translate_title = True
        self.feed.save()
        for title in (None, "", "  "):
            with self.subTest(title=title):
                self.feed.entries.update(original_title=title)
                with (
                    patch.object(OpenAIAgent, "translate") as provider,
                    patch("core.tasks.utils.time.sleep") as sleep,
                ):
                    handle_feeds_translation([self.feed], target_field="title")
                    provider.assert_not_called()
                    sleep.assert_not_called()
                self.feed.refresh_from_db()
                self.assertTrue(self.feed.translation_status)

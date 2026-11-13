import json
from datetime import timedelta
from unittest.mock import Mock, patch

from django.test import TestCase
from django.utils import timezone

from core.actions import clean_filter_results
from core.forms.filter_form import FilterForm
from core.models import Entry, Feed, Filter, FilterResult, OpenAIAgent


class FilterResultPreservationTests(TestCase):
    def setUp(self):
        self.agent = OpenAIAgent.objects.create(
            name="Original agent", api_key="test", model="old-model", valid=True
        )
        self.new_agent = OpenAIAgent.objects.create(
            name="New agent", api_key="test", model="new-model", valid=True
        )
        self.feeds = [
            Feed.objects.create(name=f"Feed {i}", feed_url=f"https://example.com/{i}")
            for i in range(2)
        ]
        self.entries = [
            Entry.objects.create(
                feed=feed,
                link=f"https://example.com/old/{i}",
                original_title=f"Old article {i}",
                original_content="Old content",
                pubdate=timezone.now() - timedelta(days=180),
            )
            for i, feed in enumerate(self.feeds)
        ]
        self.rule = self.make_rule()

    def make_rule(self):
        rule = Filter.objects.create(
            name="Shared filter",
            agent=self.agent,
            filter_method=Filter.AI_ONLY,
            filter_prompt="Original criterion",
        )
        for feed, entry, passed in zip(self.feeds, self.entries, (True, False)):
            feed.filters.add(rule)
            FilterResult.objects.create(filter=rule, entry=entry, passed=passed)
        return rule

    def decisions(self, rule):
        return list(
            rule.results.order_by("entry_id").values_list(
                "id", "entry_id", "passed", "last_updated"
            )
        )

    def test_configuration_edits_preserve_decisions_across_feeds(self):
        changes = {
            "agent_id": self.new_agent.pk,
            "filter_prompt": "New criterion",
            "filter_method": Filter.BOTH,
            "filter_original_title": False,
            "filter_original_content": False,
            "filter_translated_title": True,
            "filter_translated_content": True,
        }
        for field, value in changes.items():
            with self.subTest(field=field):
                rule = self.make_rule()
                before = self.decisions(rule)
                setattr(rule, field, value)
                rule.save()
                rule.refresh_from_db()
                self.assertEqual(getattr(rule, field), value)
                self.assertEqual(self.decisions(rule), before)
                with patch.object(OpenAIAgent, "filter") as ai:
                    passed = rule.apply_filter(Entry.objects.all())
                    self.assertEqual(list(passed), [self.entries[0]])
                    ai.assert_not_called()

    def test_form_edits_use_new_settings_only_for_unclassified_entries(self):
        form = FilterForm(
            instance=self.rule,
            data={
                "name": self.rule.name,
                "operation": Filter.EXCLUDE,
                "filter_method": Filter.AI_ONLY,
                "agent": self.new_agent.pk,
                "filter_prompt": "Changed criterion",
                "total_tokens": 0,
                "target_field": ["original_title"],
                "keywords": "",
            },
        )
        self.assertTrue(form.is_valid(), form.errors)
        rule = form.save()
        form.save_m2m()
        new_entry = Entry.objects.create(
            feed=self.feeds[0],
            link="https://example.com/new",
            original_title="New article",
            original_content="New content",
        )
        with patch.object(
            OpenAIAgent,
            "filter",
            autospec=True,
            return_value={"passed": True, "tokens": 3},
        ) as ai:
            passed = rule.apply_filter(Entry.objects.all())
            self.assertCountEqual(passed, [self.entries[0], new_entry])
            ai.assert_called_once()
            args, kwargs = ai.call_args
            self.assertEqual(args[0].pk, self.new_agent.pk)
            self.assertEqual(args[0].model, "new-model")
            self.assertEqual(kwargs["system_prompt"], "Changed criterion")
            self.assertEqual(
                json.loads(kwargs["text"]), {"original_title": "New article"}
            )
            rule.refresh_from_db()
            self.assertEqual(rule.total_tokens, 3)
            self.assertEqual(rule.results.count(), 3)
            self.assertCountEqual(rule.apply_filter(Entry.objects.all()), passed)
            ai.assert_called_once()

    def test_explicit_clear_reprocesses_only_the_selected_filter(self):
        other_rule = self.make_rule()
        other_decisions = self.decisions(other_rule)
        self.rule.filter_prompt = "New criterion"
        self.rule.save()
        clean_filter_results(Mock(), Mock(), Filter.objects.filter(pk=self.rule.pk))
        self.assertFalse(self.rule.results.exists())
        self.assertEqual(self.decisions(other_rule), other_decisions)
        with patch.object(
            OpenAIAgent, "filter", return_value={"passed": False, "tokens": 2}
        ) as ai:
            self.assertFalse(self.rule.apply_filter(Entry.objects.all()).exists())
            self.assertEqual(ai.call_count, 2)
        self.assertEqual(self.rule.results.filter(passed=False).count(), 2)

    def test_changed_article_still_gets_reevaluated(self):
        decision = self.rule.results.get(entry=self.entries[0])
        self.entries[0].updated = decision.last_updated + timedelta(seconds=1)
        self.entries[0].save(update_fields=["updated"])
        with patch.object(
            OpenAIAgent, "filter", return_value={"passed": False, "tokens": 2}
        ) as ai:
            self.assertFalse(self.rule.apply_filter(Entry.objects.all()).exists())
            ai.assert_called_once()
        decision.refresh_from_db()
        self.assertFalse(decision.passed)

    def test_deleting_and_refetching_entry_removes_its_old_decision(self):
        other_decision = self.rule.results.get(entry=self.entries[1])
        link = self.entries[0].link
        self.entries[0].delete()
        self.assertEqual(self.rule.results.count(), 1)
        entry = Entry.objects.create(
            feed=self.feeds[0],
            link=link,
            original_title="Refetched",
            original_content="Refetched content",
        )
        with patch.object(
            OpenAIAgent, "filter", return_value={"passed": True, "tokens": 2}
        ) as ai:
            self.assertEqual(list(self.rule.apply_filter(Entry.objects.all())), [entry])
            ai.assert_called_once()
        self.assertTrue(
            self.rule.results.filter(pk=other_decision.pk, passed=False).exists()
        )

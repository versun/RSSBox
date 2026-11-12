from datetime import timedelta
from unittest.mock import Mock, patch
from xml.etree import ElementTree

from django.contrib.admin.sites import AdminSite
from django.test import TestCase
from django.utils import timezone

from core.actions import clean_filter_results
from core.admin.filter_admin import FilterAdmin
from core.cache import generate_atom_feed, merge_feeds_into_one_atom
from core.forms.filter_form import FilterForm
from core.models import Entry, Feed, Filter, FilterResult, OpenAIAgent, Tag


class FilterEvaluationWindowTests(TestCase):
    def setUp(self):
        self.agent = OpenAIAgent.objects.create(
            name="Window", api_key="test", valid=True
        )
        self.rule = Filter.objects.create(
            name="AI",
            agent=self.agent,
            filter_method=Filter.AI_ONLY,
            filter_prompt="Old rule",
        )
        self.feeds = [
            Feed.objects.create(
                name=f"Feed {i}",
                slug=f"feed-{i}",
                feed_url=f"https://example.com/{i}",
                max_posts=2,
            )
            for i in range(2)
        ]
        self.entries = []
        for feed in self.feeds:
            feed.filters.add(self.rule)
            for i in range(5):
                self.entries.append(
                    Entry.objects.create(
                        feed=feed,
                        link=f"https://example.com/{feed.pk}/{i}",
                        original_title=f"Article {feed.pk}-{i}",
                        original_content="Content",
                        pubdate=timezone.now() - timedelta(days=i),
                    )
                )

    def form(self, reevaluate=False):
        data = {
            "name": self.rule.name,
            "operation": Filter.EXCLUDE,
            "filter_method": Filter.AI_ONLY,
            "agent": self.agent.pk,
            "filter_prompt": "New rule",
            "total_tokens": 0,
            "target_field": ["original_title"],
            "keywords": "",
        }
        if reevaluate:
            data["reevaluate_existing"] = True
        form = FilterForm(instance=self.rule, data=data)
        self.assertTrue(form.is_valid(), form.errors)
        return form

    def seed_decisions(self):
        for entry in self.entries:
            FilterResult.objects.get_or_create(
                filter=self.rule, entry=entry, defaults={"passed": True}
            )

    def output_count(self, xml):
        self.assertIsNotNone(xml)
        return len(
            ElementTree.fromstring(xml).findall("{http://www.w3.org/2005/Atom}entry")
        )

    def test_default_form_edit_keeps_decisions_across_feeds(self):
        self.seed_decisions()
        form = self.form()
        self.assertFalse(form.fields["reevaluate_existing"].initial)
        form.save()
        with patch.object(OpenAIAgent, "filter") as ai:
            for feed in self.feeds:
                self.assertEqual(self.output_count(generate_atom_feed(feed)), 2)
            ai.assert_not_called()
        self.assertEqual(self.rule.results.count(), 10)

    def test_opt_in_is_honored_for_immediate_and_admin_deferred_save(self):
        admin = FilterAdmin(Filter, AdminSite())
        self.assertIn("reevaluate_existing", admin.fieldsets[2][1]["fields"])
        for deferred in (False, True):
            with self.subTest(deferred=deferred):
                self.seed_decisions()
                form = self.form(reevaluate=True)
                if deferred:
                    obj = form.save(commit=False)
                    self.assertEqual(self.rule.results.count(), 10)
                    admin.save_model(Mock(), obj, form, change=True)
                    self.assertEqual(self.rule.results.count(), 10)
                    admin.save_related(Mock(), form, [], change=True)
                else:
                    form.save()
                self.assertEqual(self.rule.results.count(), 0)
                with patch.object(
                    OpenAIAgent, "filter", return_value={"passed": True, "tokens": 1}
                ) as ai:
                    for feed in self.feeds:
                        self.assertEqual(self.output_count(generate_atom_feed(feed)), 2)
                    self.assertEqual(ai.call_count, 4)
                    # A second save_m2m must not erase newly generated results.
                    form.save_m2m()
                    self.assertEqual(self.rule.results.count(), 4)

    def test_manual_reset_only_evaluates_recent_window(self):
        self.seed_decisions()
        clean_filter_results(Mock(), Mock(), Filter.objects.filter(pk=self.rule.pk))
        with patch.object(
            OpenAIAgent, "filter", return_value={"passed": True, "tokens": 1}
        ) as ai:
            for feed in self.feeds:
                self.assertEqual(self.output_count(generate_atom_feed(feed)), 2)
            self.assertEqual(ai.call_count, 4)
        evaluated = set(self.rule.results.values_list("entry_id", flat=True))
        self.assertEqual(
            evaluated,
            {
                e.pk
                for e in (
                    self.entries[0],
                    self.entries[1],
                    self.entries[5],
                    self.entries[6],
                )
            },
        )

    def test_rejected_candidates_are_not_backfilled_from_history(self):
        with patch.object(
            OpenAIAgent, "filter", return_value={"passed": False, "tokens": 1}
        ) as ai:
            self.assertEqual(self.output_count(generate_atom_feed(self.feeds[0])), 0)
            self.assertEqual(ai.call_count, 2)

    def test_keyword_only_feeds_keep_existing_selection_behavior(self):
        self.rule.filter_method = Filter.KEYWORD_ONLY
        self.rule.operation = Filter.INCLUDE
        self.rule.keywords = "historical"
        self.rule.save()
        old = self.entries[4]
        old.original_title = "historical match"
        old.save()
        with patch.object(OpenAIAgent, "filter") as ai:
            xml = generate_atom_feed(self.feeds[0])
            self.assertEqual(self.output_count(xml), 1)
            self.assertIn("historical match", xml)
            ai.assert_not_called()

    def test_zero_feed_limit_makes_no_ai_calls(self):
        self.feeds[0].max_posts = 0
        self.feeds[0].save()
        with patch.object(OpenAIAgent, "filter") as ai:
            self.assertEqual(self.output_count(generate_atom_feed(self.feeds[0])), 0)
            ai.assert_not_called()

    def test_tag_ai_evaluation_is_bounded_before_filtering(self):
        tag = Tag.objects.create(name="Bounded tag")
        tag.filters.add(self.rule)
        for feed in self.feeds:
            feed.max_posts = 70
            feed.save()
            feed.tags.add(tag)
            Entry.objects.bulk_create(
                [
                    Entry(
                        feed=feed,
                        link=f"https://example.com/{feed.pk}/tag/{i}",
                        original_title=f"Tag {i}",
                        original_content="Content",
                        pubdate=timezone.now() - timedelta(minutes=i),
                    )
                    for i in range(70)
                ]
            )
        with patch.object(
            OpenAIAgent, "filter", return_value={"passed": False, "tokens": 1}
        ) as ai:
            self.assertEqual(
                self.output_count(merge_feeds_into_one_atom(tag.slug, self.feeds)), 0
            )
            self.assertEqual(ai.call_count, 100)
        self.assertEqual(self.rule.results.count(), 100)

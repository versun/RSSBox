"""Repair misleading pending rows without paying for another AI call."""
from unittest.mock import patch

from django.test import TestCase, RequestFactory
from django.utils import timezone

from core.admin import core_admin_site
from core.admin.entry_admin import EntryTaskStateAdmin, retry_entry_tasks_now
from core.models import Entry, EntryTaskState, Feed, OpenAIAgent
from core.tasks.translate_feeds import translate_feed
from core.tasks.summarize_feeds import summarize_feed
from core.tasks.utils import reset_entry_task_states_queryset


class CompletedTaskReconciliationTests(TestCase):
    def setUp(self):
        self.agent = OpenAIAgent.objects.create(name="test", api_key="test", model="test")
        self.feed = Feed.objects.create(
            name="test", feed_url="https://example.com/feed", translator=self.agent,
            summarizer=self.agent, translate_title=True, translate_content=True,
        )
        self.entry = Entry.objects.create(
            feed=self.feed, guid="one", link="https://example.com/one",
            original_title="Hello", original_content="<p>Hello</p>",
            pubdate=timezone.now(),
        )

    def test_reset_keeps_persisted_results_succeeded_for_all_stages(self):
        self.entry.translated_title = "你好"
        self.entry.translated_content = "<p>你好</p>"
        self.entry.ai_summary = "摘要"
        self.entry.save()
        for stage in ("title", "content", "summary"):
            EntryTaskState.objects.create(entry=self.entry, stage=stage, status="failed")
        self.assertEqual(reset_entry_task_states_queryset(EntryTaskState.objects.all()), 0)
        self.assertEqual(EntryTaskState.objects.filter(status="succeeded").count(), 3)

    def test_translation_skip_reconciles_state_without_ai_call(self):
        self.entry.translated_title = "你好"
        self.entry.save()
        state = EntryTaskState.objects.create(entry=self.entry, stage="title", status="pending")
        with patch.object(OpenAIAgent, "translate") as translate:
            self.assertEqual(translate_feed(self.feed, "title"), 0)
        translate.assert_not_called()
        state.refresh_from_db()
        self.assertEqual(state.status, "succeeded")

    def test_summary_skip_reconciles_state_without_ai_call(self):
        self.entry.ai_summary = "摘要"
        self.entry.save()
        state = EntryTaskState.objects.create(entry=self.entry, stage="summary", status="pending")
        with patch.object(OpenAIAgent, "summarize") as summarize:
            self.assertTrue(summarize_feed(self.feed))
        summarize.assert_not_called()
        state.refresh_from_db()
        self.assertEqual(state.status, "succeeded")

    def test_retry_existing_result_does_not_submit_a_worker(self):
        self.entry.translated_content = "<p>你好</p>"
        self.entry.save()
        state = EntryTaskState.objects.create(entry=self.entry, stage="content", status="pending")
        modeladmin = EntryTaskStateAdmin(EntryTaskState, core_admin_site)
        with patch.object(modeladmin, "message_user"), patch(
            "core.admin.entry_admin.task_manager.submit_task"
        ) as submit, self.captureOnCommitCallbacks(execute=True):
            retry_entry_tasks_now(modeladmin, RequestFactory().post("/"), EntryTaskState.objects.all())
        submit.assert_not_called()
        state.refresh_from_db()
        self.assertEqual(state.status, "succeeded")

    def test_explicitly_cleared_result_can_be_reset(self):
        state = EntryTaskState.objects.create(entry=self.entry, stage="title", status="succeeded")
        self.assertEqual(reset_entry_task_states_queryset(EntryTaskState.objects.all()), 1)
        state.refresh_from_db()
        self.assertEqual(state.status, "pending")

    def test_incomplete_manual_pause_is_preserved(self):
        state = EntryTaskState.objects.create(
            entry=self.entry, stage="title", status="paused", paused_manually=True
        )
        reset_entry_task_states_queryset(EntryTaskState.objects.all())
        state.refresh_from_db()
        self.assertEqual(state.status, "paused")

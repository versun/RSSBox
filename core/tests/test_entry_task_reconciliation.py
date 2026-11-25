"""Repair misleading pending rows without paying for another AI call."""
from unittest.mock import patch

from django.contrib import messages
from django.db import connection
from django.test import TestCase, RequestFactory
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.admin import core_admin_site
from core.admin.entry_admin import (
    EntryTaskStateAdmin, resume_entry_tasks, retry_entry_tasks_now,
)
from core.models import Entry, EntryTaskState, Feed, OpenAIAgent
from core.tasks.translate_feeds import translate_feed
from core.tasks.summarize_feeds import summarize_feed
from core.tasks.utils import (
    get_entry_task_state_map,
    reconcile_completed_entry_task_states,
    reset_entry_task_states_queryset,
)


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

    @staticmethod
    def write_queries(queries):
        return [
            query["sql"] for query in queries
            if query["sql"].lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE"))
        ]

    def test_incomplete_failed_and_paused_tasks_are_read_without_writes(self):
        for stage in ("title", "content", "summary"):
            EntryTaskState.objects.create(entry=self.entry, stage=stage, status="failed")
        for status in ("failed", "paused"):
            EntryTaskState.objects.update(status=status, paused_manually=status == "paused")
            for stage in ("title", "content", "summary"):
                with self.subTest(status=status, stage=stage):
                    with CaptureQueriesContext(connection) as queries:
                        states = get_entry_task_state_map(self.feed, stage)
                    self.assertEqual(states[self.entry.pk].status, status)
                    self.assertEqual(self.write_queries(queries), [])

    def test_completed_stages_reconcile_in_one_update_preserving_audit(self):
        self.entry.translated_title = "你好"
        self.entry.translated_content = "<p>你好</p>"
        self.entry.ai_summary = "摘要"
        self.entry.save()
        attempted_at = timezone.now()
        for stage in ("title", "content", "summary"):
            EntryTaskState.objects.create(
                entry=self.entry, stage=stage, status="failed",
                attempt_count=2, last_attempt_at=attempted_at,
                estimated_unbilled_tokens=123,
            )
        with CaptureQueriesContext(connection) as queries:
            repaired = reconcile_completed_entry_task_states(EntryTaskState.objects.all())
        self.assertEqual(repaired, 3)
        self.assertEqual(len(self.write_queries(queries)), 1)
        for state in EntryTaskState.objects.all():
            self.assertEqual(state.status, "succeeded")
            self.assertEqual(state.last_attempt_at, attempted_at)
            self.assertEqual(state.estimated_unbilled_tokens, 123)
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(
                reconcile_completed_entry_task_states(EntryTaskState.objects.all()), 0
            )
        self.assertEqual(self.write_queries(queries), [])

    def test_completed_condition_preserves_stage_empty_value_semantics(self):
        self.entry.translated_title = ""
        self.entry.translated_content = ""
        # The summary worker treats NULL (not an empty string) as missing.
        self.entry.ai_summary = ""
        self.entry.save()
        for stage in ("title", "content", "summary"):
            EntryTaskState.objects.create(entry=self.entry, stage=stage, status="failed")
        self.assertEqual(
            reconcile_completed_entry_task_states(EntryTaskState.objects.all()), 1
        )
        self.assertEqual(
            dict(EntryTaskState.objects.values_list("stage", "status")),
            {"title": "failed", "content": "failed", "summary": "succeeded"},
        )

    def test_retry_succeeded_tasks_with_cleared_results_submits_worker(self):
        modeladmin = EntryTaskStateAdmin(EntryTaskState, core_admin_site)
        for stage, field, empty in (
            ("title", "translated_title", None),
            ("title", "translated_title", ""),
            ("content", "translated_content", None),
            ("content", "translated_content", ""),
            ("summary", "ai_summary", None),
        ):
            with self.subTest(stage=stage, empty=empty):
                EntryTaskState.objects.all().delete()
                setattr(self.entry, field, empty)
                self.entry.save(update_fields=[field])
                state = EntryTaskState.objects.create(
                    entry=self.entry, stage=stage, status="succeeded",
                )
                with patch.object(modeladmin, "message_user"), patch(
                    "core.admin.entry_admin.task_manager.submit_task"
                ) as submit, self.captureOnCommitCallbacks(execute=True):
                    retry_entry_tasks_now(
                        modeladmin, RequestFactory().post("/"), EntryTaskState.objects.all()
                    )
                submit.assert_called_once()
                self.assertEqual(
                    submit.call_args.args[2:],
                    (self.feed.pk, {stage: [self.entry.pk]}),
                )
                state.refresh_from_db()
                self.assertEqual(state.status, "pending")

    def test_resume_completed_tasks_reports_existing_results(self):
        self.entry.translated_title = "你好"
        self.entry.save()
        state = EntryTaskState.objects.create(
            entry=self.entry, stage="title", status="failed",
        )
        modeladmin = EntryTaskStateAdmin(EntryTaskState, core_admin_site)
        with patch.object(modeladmin, "message_user") as message:
            resume_entry_tasks(
                modeladmin, RequestFactory().post("/"), EntryTaskState.objects.all()
            )
        state.refresh_from_db()
        self.assertEqual(state.status, "succeeded")
        self.assertEqual(message.call_args.args[2], messages.INFO)
        self.assertIn("already have results", str(message.call_args.args[1]))

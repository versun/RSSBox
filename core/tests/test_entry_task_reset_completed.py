"""Resetting a retry budget must not turn an already-completed stage pending.

Follow-up to issue #186: resume / retry-now on a task whose result already
exists used to set it to pending; workers then skipped the entry (result
present) and never cleared the state, so it stayed pending forever.
"""

from unittest.mock import patch

from django.test import RequestFactory, TestCase
from django.utils import timezone

from core.admin import core_admin_site
from core.admin.entry_admin import EntryTaskStateAdmin, retry_entry_tasks_now
from core.models import Entry, EntryTaskState, Feed, OpenAIAgent
from core.tasks.utils import reset_entry_task_states_queryset

STAGE_FIELDS = {
    "title": "translated_title",
    "content": "translated_content",
    "summary": "ai_summary",
}


class ResetCompletedEntryTaskTests(TestCase):
    def setUp(self):
        agent = OpenAIAgent.objects.create(name="a", api_key="k", model="m")
        self.feed = Feed.objects.create(
            name="f",
            feed_url="https://example.com/feed",
            translator=agent,
            summarizer=agent,
        )
        self.entry = Entry.objects.create(
            feed=self.feed,
            guid="1",
            link="https://example.com/1",
            original_title="Hello",
            original_content="<p>Hello</p>",
            pubdate=timezone.now(),
        )

    def _state(self, stage, status="failed", **kwargs):
        return EntryTaskState.objects.create(
            entry=self.entry, stage=stage, status=status, **kwargs
        )

    def _retry_now(self):
        modeladmin = EntryTaskStateAdmin(EntryTaskState, core_admin_site)
        with (
            patch.object(modeladmin, "message_user"),
            patch("core.admin.entry_admin.task_manager.submit_task") as submit,
            self.captureOnCommitCallbacks(execute=True),
        ):
            retry_entry_tasks_now(
                modeladmin, RequestFactory().post("/"), EntryTaskState.objects.all()
            )
        return submit

    def test_reset_marks_stages_with_results_succeeded(self):
        Entry.objects.filter(pk=self.entry.pk).update(
            translated_title="你好", translated_content="<p>你好</p>", ai_summary="摘要"
        )
        attempted_at = timezone.now()
        for stage in STAGE_FIELDS:
            self._state(
                stage,
                attempt_count=3,
                last_attempt_at=attempted_at,
                estimated_unbilled_tokens=123,
                paused_manually=True,
            )

        updated = reset_entry_task_states_queryset(
            EntryTaskState.objects.all(), include_manual_pause=True
        )

        self.assertEqual(updated, 0)
        for state in EntryTaskState.objects.all():
            self.assertEqual(state.status, EntryTaskState.STATUS_SUCCEEDED)
            self.assertEqual(state.attempt_count, 0)
            self.assertFalse(state.paused_manually)
            # Audit fields of the last real attempt are kept.
            self.assertEqual(state.last_attempt_at, attempted_at)
            self.assertEqual(state.estimated_unbilled_tokens, 123)

    def test_missing_results_follow_worker_semantics(self):
        # Title/content are missing when empty; the summary only when NULL.
        Entry.objects.filter(pk=self.entry.pk).update(
            translated_title="", translated_content=None, ai_summary=""
        )
        for stage in STAGE_FIELDS:
            self._state(stage, status="succeeded")

        updated = reset_entry_task_states_queryset(EntryTaskState.objects.all())

        self.assertEqual(updated, 2)
        self.assertEqual(
            dict(EntryTaskState.objects.values_list("stage", "status")),
            {"title": "pending", "content": "pending", "summary": "succeeded"},
        )

    def test_retry_now_skips_completed_and_submits_missing(self):
        Entry.objects.filter(pk=self.entry.pk).update(translated_title="你好")
        self._state("title")
        self._state("content")

        submit = self._retry_now()

        submit.assert_called_once()
        self.assertEqual(
            submit.call_args.args[2:], (self.feed.pk, {"content": [self.entry.pk]})
        )
        self.assertEqual(
            dict(EntryTaskState.objects.values_list("stage", "status")),
            {"title": "succeeded", "content": "pending"},
        )

    def test_retry_now_with_only_completed_tasks_submits_nothing(self):
        Entry.objects.filter(pk=self.entry.pk).update(ai_summary="摘要")
        self._state("summary", status="paused", paused_manually=True)

        submit = self._retry_now()

        submit.assert_not_called()
        self.assertEqual(
            EntryTaskState.objects.get().status, EntryTaskState.STATUS_SUCCEEDED
        )

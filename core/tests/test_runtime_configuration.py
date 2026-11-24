from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, RequestFactory, override_settings
from django.utils import timezone, translation

from core.admin import core_admin_site
from core.admin.entry_admin import EntryTaskStateAdmin, TaskAttentionFilter
from core.admin.runtime_configuration_admin import RuntimeConfigurationAdmin
from core.models import RuntimeConfiguration, Entry, EntryTaskState, Feed, OpenAIAgent
from core.runtime_policy import get_runtime_policy
from core.tasks.utils import record_entry_stage_failure


@override_settings(OPENAI_API_TIMEOUT=900.0, ENTRY_MAX_RETRY_ATTEMPTS=3,
                   ENTRY_RETRY_BASE_DELAY_SECONDS=3600, ENTRY_RETRY_MAX_DELAY_SECONDS=86400)
class RuntimeConfigurationTests(TestCase):
    def setUp(self):
        self.agent = OpenAIAgent.objects.create(name="test", api_key="test", model="test")
        self.feed = Feed.objects.create(name="test", feed_url="https://example.com/feed", translator=self.agent)
        self.entry = Entry.objects.create(feed=self.feed, guid="one", link="https://example.com/one",
                                          original_title="Hello", pubdate=timezone.now())

    def test_missing_configuration_inherits_env_without_writing(self):
        self.assertEqual(get_runtime_policy().request_timeout, 900)
        self.assertEqual(get_runtime_policy().max_attempts, 3)
        self.assertFalse(RuntimeConfiguration.objects.exists())

    def test_committed_updates_are_visible_without_cache_or_restart(self):
        config = RuntimeConfiguration.objects.create(request_timeout=600)
        self.assertEqual(get_runtime_policy().request_timeout, 600)
        config.request_timeout = 1200
        config.save()
        self.assertEqual(get_runtime_policy().request_timeout, 1200)
        config.request_timeout = None
        config.save()
        self.assertEqual(get_runtime_policy().request_timeout, 900)

    def test_agent_override_wins_over_database_and_env(self):
        RuntimeConfiguration.objects.create(request_timeout=600)
        with patch("core.models.agent.OpenAI") as client:
            self.agent._init()
            self.assertEqual(client.call_args.kwargs["timeout"], 600)
            self.agent.request_timeout = 300
            self.agent._init()
            self.assertEqual(client.call_args.kwargs["timeout"], 300)

    def test_runtime_schedule_and_failed_round_limit(self):
        RuntimeConfiguration.objects.create(max_attempts=4, retry_base_seconds=60, retry_max_seconds=90)
        now = timezone.now()
        with patch("core.tasks.utils.timezone.now", return_value=now):
            for attempt, delay in ((1, 60), (2, 90), (3, 90)):
                state = record_entry_stage_failure(self.entry, "title", TimeoutError("slow"))
                self.assertEqual(state.attempt_count, attempt)
                self.assertEqual(state.status, "failed")
                self.assertEqual(state.next_retry_at, now + timedelta(seconds=delay))
            state = record_entry_stage_failure(self.entry, "title", TimeoutError("slow"))
            self.assertEqual(state.status, "paused")
            self.assertIsNone(state.next_retry_at)

    def test_settings_change_does_not_rewrite_existing_retry_timestamp(self):
        state = record_entry_stage_failure(self.entry, "title", TimeoutError("slow"))
        retry_at = state.next_retry_at
        RuntimeConfiguration.objects.create(retry_base_seconds=60)
        state.refresh_from_db()
        self.assertEqual(state.next_retry_at, retry_at)

    def test_invalid_timeout_delays_and_singleton_are_rejected(self):
        for value in (0, -1, float("nan"), float("inf")):
            with self.subTest(timeout=value), self.assertRaises(ValidationError):
                RuntimeConfiguration(request_timeout=value).full_clean()
        with self.assertRaises(ValidationError):
            RuntimeConfiguration(retry_base_seconds=100, retry_max_seconds=50).full_clean()
        with self.assertRaises(ValidationError):
            RuntimeConfiguration(id=2).full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            RuntimeConfiguration.objects.create(id=2)

    def test_attention_filter_is_visibility_not_execution_budget(self):
        RuntimeConfiguration.objects.create(attention_after_attempts=2)
        EntryTaskState.objects.create(entry=self.entry, stage="title", status="failed", attempt_count=1)
        EntryTaskState.objects.create(entry=self.entry, stage="content", status="paused", attempt_count=1)
        EntryTaskState.objects.create(entry=self.entry, stage="summary", status="succeeded")
        request = RequestFactory().get("/")
        modeladmin = EntryTaskStateAdmin(EntryTaskState, core_admin_site)
        view = TaskAttentionFilter(request, {}, EntryTaskState, modeladmin)
        self.assertEqual(list(view.queryset(request, EntryTaskState.objects.all()).values_list("stage", flat=True)), ["content"])
        # The hidden failure still exists for scheduling and audit.
        self.assertEqual(EntryTaskState.objects.count(), 3)
        request = RequestFactory().get("/?status__exact=succeeded")
        view = TaskAttentionFilter(request, {}, EntryTaskState, modeladmin)
        self.assertEqual(view.queryset(request, EntryTaskState.objects.all()).count(), 3)

    def test_admin_get_is_read_only_and_permissions_are_enforced(self):
        self.client.force_login(User.objects.create_superuser("admin", "a@example.com", "test"))
        response = self.client.get("/core/runtimeconfiguration/add/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(RuntimeConfiguration.objects.exists())
        user = User.objects.create_user("staff", password="test", is_staff=True)
        self.client.force_login(user)
        self.assertEqual(self.client.get("/core/runtimeconfiguration/add/").status_code, 403)

    def test_singleton_admin_has_no_second_add_or_delete(self):
        request = RequestFactory().get("/")
        request.user = User.objects.create_superuser("admin", "a@example.com", "test")
        modeladmin = RuntimeConfigurationAdmin(RuntimeConfiguration, core_admin_site)
        self.assertTrue(modeladmin.has_add_permission(request))
        config = RuntimeConfiguration.objects.create()
        self.assertFalse(modeladmin.has_add_permission(request))
        self.assertFalse(modeladmin.has_delete_permission(request, config))

    def test_chinese_runtime_settings_are_translated(self):
        with translation.override("zh-hans"):
            self.assertEqual(translation.gettext("Global AI settings"), "全局 AI 设置")

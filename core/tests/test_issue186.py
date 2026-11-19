"""Regression and acceptance tests for issue #186.

Covers:
1. Client timeout does not immediately replay 3x in-round and does not replay
   indefinitely across hourly runs (per-entry backoff and auto-pause).
2. Single-entry pause does not affect other entries; resuming/recovering does
   not re-translate already-succeeded title or content.
3. Large-context models (e.g. 1M max_tokens) decouple target chunk size and
   output token cap so long articles are split cleanly and max_completion_tokens
   never explodes.
4. HTML-safe chunking preserves tags, links, code blocks, and notranslate spans.
5. Cross-round / post-restart chunk resume cache reuses completed chunks without
   double-billing, and invalidates when source text or config changes.
6. Error classification (timeout, finish_reason=length truncation, 429, 5xx,
   empty response, PersistError) and immediate persistence of fetched full text.
7. Admin actions (pause/resume/single-entry retry, force-update respecting manual
   pause, clean_translated_content, cleanup_entries pruning expired chunk caches).
"""

from datetime import timedelta
import html as html_lib
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
from bs4 import BeautifulSoup
from django.contrib.auth.models import User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.cache import cache as django_cache
from django.core.exceptions import ValidationError
from django.db import DatabaseError
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone
from openai import APITimeoutError, InternalServerError, RateLimitError

from core.actions import clean_translated_content, feed_force_update
from core.admin import core_admin_site
from core.admin.entry_admin import (
    EntryTaskStateAdmin,
    pause_entry_tasks,
    resume_entry_tasks,
    retry_entry_tasks_now,
    retry_feed_entry_stages,
    retry_single_entry_stage,
)
from core.admin.feed_admin import FeedAdmin
from core.management.commands.cleanup_entries import cleanup_all_feeds
from core.models import (
    Digest,
    Entry,
    EntryTaskState,
    Feed,
    OpenAIAgent,
    Tag,
    TranslationChunkCache,
)
from core.tasks.fetch_feeds import _process_feed_entries, _source_hash
from core.tasks.generate_digests import _ensure_entries_have_summaries
from core.tasks.summarize_feeds import handle_feeds_summary, summarize_feed
from core.tasks.translate_feeds import handle_feeds_translation, translate_feed
from utils.text_handler import chunk_html, get_token_count


def _mock_response(
    text: str = "Translated",
    tokens: int = 20,
    reason: str = "stop",
    request_id: str = "req_123",
):
    return SimpleNamespace(
        id=request_id,
        choices=[
            SimpleNamespace(
                finish_reason=reason,
                message=SimpleNamespace(content=text),
            )
        ],
        usage=SimpleNamespace(
            total_tokens=tokens,
            prompt_tokens=tokens // 2,
            completion_tokens=tokens - tokens // 2,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=5),
        ),
    )


def _timeout_error():
    return APITimeoutError(request=httpx.Request("POST", "https://api.example.com"))


def _make_long_html(paragraphs: int = 30) -> str:
    parts = []
    for i in range(paragraphs):
        parts.append(
            f'<p>Paragraph {i}: The quick brown fox discusses constitutional law '
            f'and liberty in detail with <a href="https://example.com/ref.{i}.html">'
            f'source link {i}</a> and repeated commentary about judicial review.</p>'
        )
    parts.append('<pre class="notranslate"><code>def f(x):\n    return x + 1\n</code></pre>')
    return "<html><body>" + "\n".join(parts) + "</body></html>"


class HtmlSafeChunkingTests(TestCase):
    def test_chunk_html_preserves_tags_links_and_code_blocks(self):
        html = _make_long_html(paragraphs=25)
        max_tokens = 250
        chunks = chunk_html(html, max_tokens=max_tokens)

        self.assertGreater(len(chunks), 2)
        for chunk in chunks:
            self.assertLessEqual(get_token_count(chunk), max_tokens)
            # Every chunk must parse without broken tags and keep links valid.
            soup = BeautifulSoup(chunk, "lxml")
            for link in soup.find_all("a"):
                self.assertTrue(link.get("href", "").startswith("https://example.com/ref."))
                self.assertTrue(link.get("href", "").endswith(".html"))

        combined = "\n".join(chunks)
        self.assertIn('<pre class="notranslate"><code>def f(x):', combined)
        for i in range(25):
            self.assertIn(f"Paragraph {i}:", combined)

    def test_chunk_html_splits_giant_single_paragraph_safely(self):
        giant = (
            '<p id="intro">'
            + " ".join(f"Sentence {i} explains a legal principle clearly." for i in range(80))
            + "</p>"
        )
        chunks = chunk_html(giant, max_tokens=120)
        self.assertGreater(len(chunks), 2)
        for idx, chunk in enumerate(chunks):
            self.assertLessEqual(get_token_count(chunk), 120)
            self.assertTrue(chunk.startswith("<p"))
            self.assertTrue(chunk.endswith("</p>"))
            # id should only appear on the first fragment, not duplicated.
            if idx == 0:
                self.assertIn('id="intro"', chunk)
            else:
                self.assertNotIn('id="intro"', chunk)

    def test_chunk_html_handles_name_attribute_without_crashing(self):
        html = '<a name="sec1" href="https://example.com">' + "word " * 100 + "</a>"
        chunks = chunk_html(html, max_tokens=50)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(get_token_count(chunk), 50)
            self.assertIn('name="sec1"', chunk)
            self.assertIn('href="https://example.com"', chunk)

    def test_chunk_html_preserves_multiline_code_indentation_and_budget(self):
        code_lines = "\n".join(
            f"    if a[{i}] < b[{i}] and c[{i}] > d[{i}]: return {i}"
            for i in range(40)
        )
        html = (
            '<pre class="notranslate"><code>'
            f"{html_lib.escape(code_lines, quote=False)}"
            "</code></pre>"
        )
        chunks = chunk_html(html, max_tokens=120)
        self.assertGreater(len(chunks), 2)
        self.assertTrue(any("\n    if a[" in chunk for chunk in chunks))
        for chunk in chunks:
            self.assertLessEqual(get_token_count(chunk), 120)
            self.assertIn('<pre class="notranslate"><code>', chunk)
            self.assertIn("</code></pre>", chunk)
            self.assertIn("    if a[", chunk)

    def test_chunk_html_enforces_budget_after_html_escaping(self):
        text = " ".join(f"a&b<c>d{i}." for i in range(60))
        html = f"<p>{html_lib.escape(text, quote=False)}</p>"
        chunks = chunk_html(html, max_tokens=60)
        self.assertGreater(len(chunks), 2)
        for chunk in chunks:
            self.assertLessEqual(get_token_count(chunk), 60)
            # Re-parsing must recover valid escaped entities without torn markup.
            soup = BeautifulSoup(chunk, "lxml")
            self.assertIsNotNone(soup.find("p"))

    def test_chunk_html_block_container_avoids_triple_newlines(self):
        html = (
            "<div>\n"
            + "\n".join(f"<p>Para {i} text here.</p>" for i in range(20))
            + "\n</div>"
        )
        chunks = chunk_html(html, max_tokens=60)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertNotIn("\n\n\n", chunk)
            self.assertLessEqual(get_token_count(chunk), 60)


class RetryBudgetAndBackoffTests(TestCase):
    def setUp(self):
        self.agent = OpenAIAgent.objects.create(
            name="Large Context Model",
            api_key="test-key",
            model="gemini-3.8-flash",
            max_tokens=998540,
            max_chunk_tokens=4000,
            advanced_params={"temperature": 0.2, "reasoning_effort": "high"},
            rate_limit_rpm=0,
        )
        self.feed = Feed.objects.create(
            name="Law & Liberty",
            feed_url="https://lawliberty.org/feed",
            translator=self.agent,
            translate_title=True,
            translate_content=True,
            max_posts=20,
        )
        self.client = MagicMock()
        self.client.with_options.return_value = self.client
        self.create = self.client.chat.completions.create
        self.init = patch.object(OpenAIAgent, "_init", return_value=self.client)
        self.init.start()
        self.addCleanup(self.init.stop)
        sleep = patch("core.tasks.utils.time.sleep")
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)

    @override_settings(
        ENTRY_MAX_RETRY_ATTEMPTS=3,
        ENTRY_RETRY_BASE_DELAY_SECONDS=3600,
        ENTRY_RETRY_MAX_DELAY_SECONDS=86400,
    )
    def test_slow_timeout_never_replays_3x_and_pauses_after_budget(self):
        entry = Entry.objects.create(
            feed=self.feed,
            guid="signs-of-life",
            link="https://lawliberty.org/podcast/signs-of-life/",
            original_title="Signs of Life",
            translated_title="生机迹象",
            original_content="<p>A very long podcast transcript.</p>",
            pubdate=timezone.now(),
        )
        self.create.side_effect = _timeout_error()

        # Run 1: single attempt only (no 3x in-round retry on timeout).
        handle_feeds_translation([self.feed], target_field="content")
        self.assertEqual(self.create.call_count, 1)
        self.sleep.assert_not_called()

        state = EntryTaskState.objects.get(entry=entry, stage="content")
        self.assertEqual(state.status, EntryTaskState.STATUS_FAILED)
        self.assertEqual(state.attempt_count, 1)
        self.assertEqual(state.last_error_type, "timeout")
        self.assertGreater(state.estimated_unbilled_tokens, 0)
        self.assertIsNotNone(state.next_retry_at)
        first_retry = state.next_retry_at

        # Immediate next run before next_retry_at: skipped (0 API calls).
        handle_feeds_translation([self.feed], target_field="content")
        self.assertEqual(self.create.call_count, 1)

        # Advance past first backoff -> Attempt 2 (2x delay).
        EntryTaskState.objects.filter(pk=state.pk).update(
            next_retry_at=timezone.now() - timedelta(seconds=1)
        )
        handle_feeds_translation([self.feed], target_field="content")
        self.assertEqual(self.create.call_count, 2)
        state.refresh_from_db()
        self.assertEqual(state.attempt_count, 2)
        self.assertEqual(state.status, EntryTaskState.STATUS_FAILED)
        # Backoff doubled (~2h vs ~1h).
        self.assertGreater(
            (state.next_retry_at - timezone.now()).total_seconds(), 5400
        )
        self.assertGreater(state.next_retry_at, first_retry)

        # Advance past second backoff -> Attempt 3 hits budget -> auto-paused.
        EntryTaskState.objects.filter(pk=state.pk).update(
            next_retry_at=timezone.now() - timedelta(seconds=1)
        )
        handle_feeds_translation([self.feed], target_field="content")
        self.assertEqual(self.create.call_count, 3)
        state.refresh_from_db()
        self.assertEqual(state.attempt_count, 3)
        self.assertEqual(state.status, EntryTaskState.STATUS_PAUSED)
        self.assertIsNone(state.next_retry_at)

        # Subsequent runs: permanently skipped while paused (0 API calls).
        handle_feeds_translation([self.feed], target_field="content")
        self.assertEqual(self.create.call_count, 3)

        # Translated title was never lost or overwritten with fake content.
        entry.refresh_from_db()
        self.assertEqual(entry.translated_title, "生机迹象")
        self.assertIsNone(entry.translated_content)

    def test_paused_entry_does_not_block_other_entries_and_resume_keeps_title(self):
        paused_entry = Entry.objects.create(
            feed=self.feed,
            guid="bad",
            link="https://example.com/bad",
            original_title="Slow Article",
            translated_title="慢文章",
            original_content="<p>Slow body</p>",
            pubdate=timezone.now(),
        )
        EntryTaskState.objects.create(
            entry=paused_entry,
            stage="content",
            status=EntryTaskState.STATUS_PAUSED,
            attempt_count=3,
            paused_manually=True,
        )
        good_entry = Entry.objects.create(
            feed=self.feed,
            guid="good",
            link="https://example.com/good",
            original_title="Fast Article",
            translated_title="快文章",
            original_content="<p>Fast body</p>",
            pubdate=timezone.now() - timedelta(minutes=1),
        )
        self.create.return_value = _mock_response("<p>快速正文</p>", tokens=15)

        handle_feeds_translation([self.feed], target_field="content")

        self.assertEqual(self.create.call_count, 1)
        paused_entry.refresh_from_db()
        good_entry.refresh_from_db()
        self.feed.refresh_from_db()
        self.assertIsNone(paused_entry.translated_content)
        self.assertEqual(good_entry.translated_content, "<p>快速正文</p>")
        self.assertTrue(self.feed.translation_status)
        self.assertIsNotNone(self.feed.last_translate)

        # Resume paused entry: title must NOT be re-translated, only content runs.
        EntryTaskState.objects.filter(entry=paused_entry, stage="content").update(
            status=EntryTaskState.STATUS_PENDING,
            attempt_count=0,
            paused_manually=False,
            next_retry_at=None,
        )
        self.create.reset_mock()
        self.create.return_value = _mock_response("<p>慢文章正文</p>", tokens=25)

        handle_feeds_translation([self.feed], target_field="title")
        self.create.assert_not_called()  # title already translated!

        handle_feeds_translation([self.feed], target_field="content")
        self.assertEqual(self.create.call_count, 1)
        paused_entry.refresh_from_db()
        self.assertEqual(paused_entry.translated_title, "慢文章")
        self.assertEqual(paused_entry.translated_content, "<p>慢文章正文</p>")
        state = EntryTaskState.objects.get(entry=paused_entry, stage="content")
        self.assertEqual(state.status, EntryTaskState.STATUS_SUCCEEDED)
        self.assertEqual(state.attempt_count, 0)

    def test_large_context_model_chunks_by_target_size_and_caps_output_tokens(self):
        # 1M context window, but max_chunk_tokens=300 forces safe chunking.
        self.agent.max_tokens = 998540
        self.agent.max_chunk_tokens = 300
        self.agent.save()

        html = _make_long_html(paragraphs=20)
        self.create.return_value = _mock_response("<p>译文块</p>", tokens=30)

        entry = Entry.objects.create(
            feed=self.feed,
            guid="long-1m",
            link="https://example.com/long-1m",
            original_title="Long",
            original_content=html,
            pubdate=timezone.now(),
        )
        translate_feed(self.feed, target_field="content")

        self.assertGreater(self.create.call_count, 2)
        for call in self.create.call_args_list:
            # Must NOT derive ~787k max_completion_tokens from 1M context!
            self.assertLessEqual(call.kwargs["max_completion_tokens"], 16384)
            self.assertGreaterEqual(call.kwargs["max_completion_tokens"], 4096)

        entry.refresh_from_db()
        self.assertIsNotNone(entry.translated_content)

    def test_per_agent_request_timeout_overrides_global_setting(self):
        self.init.stop()
        self.agent.request_timeout = 42.5
        self.agent.save()
        with patch("core.models.agent.OpenAI") as mock_openai:
            self.agent._init()
        self.assertEqual(mock_openai.call_args.kwargs["timeout"], 42.5)

        # Non-finite values must fall back to OPENAI_API_TIMEOUT.
        self.agent.request_timeout = float("nan")
        with (
            override_settings(OPENAI_API_TIMEOUT=77.0),
            patch("core.models.agent.OpenAI") as mock_openai,
        ):
            self.agent._init()
        self.assertEqual(mock_openai.call_args.kwargs["timeout"], 77.0)

    @override_settings(OPENAI_MAX_OUTPUT_TOKENS=2048)
    def test_output_token_cap_respects_configured_values_below_4096(self):
        self.create.return_value = _mock_response("<p>ok</p>", tokens=10)
        result = self.agent.completions("hello world", system_prompt=None)
        self.assertEqual(result["text"], "<p>ok</p>")
        self.assertEqual(
            self.create.call_args.kwargs["max_completion_tokens"], 2048
        )

    def test_chunked_timeout_estimates_only_failing_chunk_tokens(self):
        self.agent.max_chunk_tokens = 200
        self.agent.save(update_fields=["max_chunk_tokens"])
        html = _make_long_html(paragraphs=15)
        full_tokens = get_token_count(html)
        entry = Entry.objects.create(
            feed=self.feed,
            guid="chunk-est-1",
            link="https://example.com/chunk-est-1",
            original_title="Chunk Est",
            original_content=html,
            pubdate=timezone.now(),
        )
        self.create.side_effect = [
            _mock_response("<p>chunk 1</p>", tokens=10),
            _timeout_error(),
        ]
        handle_feeds_translation([self.feed], target_field="content")
        state = EntryTaskState.objects.get(entry=entry, stage="content")
        self.assertGreater(state.estimated_unbilled_tokens, 0)
        self.assertLess(state.estimated_unbilled_tokens, full_tokens)


class ChunkResumeCacheTests(TestCase):
    def setUp(self):
        self.agent = OpenAIAgent.objects.create(
            name="Resume Agent",
            api_key="test-key",
            model="gpt-4o-mini",
            max_tokens=128000,
            max_chunk_tokens=200,
            rate_limit_rpm=0,
        )
        self.feed = Feed.objects.create(
            name="Resume Feed",
            feed_url="https://example.com/resume.xml",
            translator=self.agent,
            translate_content=True,
            max_posts=10,
        )
        self.client = MagicMock()
        self.client.with_options.return_value = self.client
        self.create = self.client.chat.completions.create
        init = patch.object(OpenAIAgent, "_init", return_value=self.client)
        init.start()
        self.addCleanup(init.stop)
        sleep = patch("core.tasks.utils.time.sleep")
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)

    def test_chunk_failure_and_process_restart_reuses_completed_chunks(self):
        html = _make_long_html(paragraphs=15)
        entry = Entry.objects.create(
            feed=self.feed,
            guid="resume-1",
            link="https://example.com/resume-1",
            original_title="Resume",
            original_content=html,
            pubdate=timezone.now(),
        )

        # Run 1: chunks 1 & 2 succeed (10 tokens each), chunk 3 times out.
        self.create.side_effect = [
            _mock_response("<p>CHUNK_1_OK</p>", tokens=10),
            _mock_response("<p>CHUNK_2_OK</p>", tokens=10),
            _timeout_error(),
        ]
        handle_feeds_translation([self.feed], target_field="content")

        self.assertEqual(self.create.call_count, 3)
        entry.refresh_from_db()
        self.feed.refresh_from_db()
        self.assertIsNone(entry.translated_content)
        self.assertEqual(self.feed.total_tokens, 20)
        self.assertEqual(
            TranslationChunkCache.objects.filter(entry=entry, stage="content").count(),
            2,
        )

        # Simulate process restart: clear in-memory cache and reload fresh DB objects.
        django_cache.clear()
        EntryTaskState.objects.filter(entry=entry, stage="content").update(
            next_retry_at=timezone.now() - timedelta(seconds=1)
        )
        fresh_feed = Feed.objects.get(pk=self.feed.pk)

        self.create.reset_mock(side_effect=True)
        call_num = {"n": 0}

        def finish_remaining(**kwargs):
            call_num["n"] += 1
            return _mock_response(f"<p>REMAINING_{call_num['n']}</p>", tokens=11)

        self.create.side_effect = finish_remaining
        handle_feeds_translation([fresh_feed], target_field="content")

        # Chunks 1 & 2 were reused from DB cache; only remaining chunks hit API.
        entry.refresh_from_db()
        fresh_feed.refresh_from_db()
        self.assertIn("<p>CHUNK_1_OK</p>", entry.translated_content)
        self.assertIn("<p>CHUNK_2_OK</p>", entry.translated_content)
        self.assertIn("<p>REMAINING_1</p>", entry.translated_content)
        # No double billing of chunks 1 & 2: total = 20 + 11 * remaining_calls.
        self.assertEqual(fresh_feed.total_tokens, 20 + 11 * call_num["n"])
        # Temporary chunk caches are cleaned up after full entry commits.
        self.assertEqual(
            TranslationChunkCache.objects.filter(entry=entry, stage="content").count(),
            0,
        )

    def test_identical_chunks_across_entries_are_isolated(self):
        html = _make_long_html(paragraphs=12)
        entry_a = Entry.objects.create(
            feed=self.feed,
            guid="iso-a",
            link="https://example.com/iso-a",
            original_title="Iso A",
            original_content=html,
            pubdate=timezone.now(),
        )
        entry_b = Entry.objects.create(
            feed=self.feed,
            guid="iso-b",
            link="https://example.com/iso-b",
            original_title="Iso B",
            original_content=html,
            pubdate=timezone.now() - timedelta(minutes=1),
        )

        # Both entries cache chunk 1 then time out on chunk 2.
        self.create.side_effect = [
            _mock_response("<p>A_CHUNK_1</p>", tokens=10),
            _timeout_error(),
            _mock_response("<p>B_CHUNK_1</p>", tokens=10),
            _timeout_error(),
        ]
        handle_feeds_translation([self.feed], target_field="content")
        self.assertEqual(
            TranslationChunkCache.objects.filter(entry=entry_a, stage="content").count(),
            1,
        )
        self.assertEqual(
            TranslationChunkCache.objects.filter(entry=entry_b, stage="content").count(),
            1,
        )

        # Completing entry_a must not delete entry_b's cached chunk.
        EntryTaskState.objects.filter(entry=entry_a, stage="content").update(
            next_retry_at=timezone.now() - timedelta(seconds=1)
        )
        self.create.reset_mock(side_effect=True)
        self.create.return_value = _mock_response("<p>A_REST</p>", tokens=5)
        translate_feed(self.feed, target_field="content", entry_ids=[entry_a.pk])
        self.assertEqual(
            TranslationChunkCache.objects.filter(entry=entry_a, stage="content").count(),
            0,
        )
        self.assertEqual(
            TranslationChunkCache.objects.filter(entry=entry_b, stage="content").count(),
            1,
        )

    def test_chunk_cache_invalidates_on_model_or_prompt_or_source_change(self):
        html = _make_long_html(paragraphs=12)
        entry = Entry.objects.create(
            feed=self.feed,
            guid="inv-1",
            link="https://example.com/inv-1",
            original_title="Inv",
            original_content=html,
            source_hash=_source_hash(html),
            pubdate=timezone.now(),
        )

        # Cache chunk 1, fail chunk 2.
        self.create.side_effect = [
            _mock_response("<p>OLD_MODEL_CHUNK_1</p>", tokens=10),
            _timeout_error(),
        ]
        handle_feeds_translation([self.feed], target_field="content")
        self.assertEqual(
            TranslationChunkCache.objects.filter(entry=entry, stage="content").count(),
            1,
        )

        # Change agent model -> old cached chunk MUST miss and be re-translated.
        self.agent.model = "gpt-4.1"
        self.agent.save(update_fields=["model"])
        EntryTaskState.objects.filter(entry=entry, stage="content").update(
            next_retry_at=timezone.now() - timedelta(seconds=1)
        )
        self.create.reset_mock(side_effect=True)
        self.create.return_value = _mock_response("<p>NEW_MODEL_CHUNK</p>", tokens=10)

        fresh_feed = Feed.objects.get(pk=self.feed.pk)
        handle_feeds_translation([fresh_feed], target_field="content")
        entry.refresh_from_db()
        self.assertNotIn("OLD_MODEL_CHUNK_1", entry.translated_content)
        self.assertIn("NEW_MODEL_CHUNK", entry.translated_content)

    def test_source_update_in_fetch_resets_state_and_clears_chunk_cache(self):
        now = timezone.now()
        entry = Entry.objects.create(
            feed=self.feed,
            guid="g-update",
            link="https://example.com/g-update",
            original_title="Old Title",
            original_content="<p>old</p>",
            original_summary="<p>old</p>",
            source_hash=_source_hash("<p>old</p>"),
            pubdate=now,
        )
        EntryTaskState.objects.create(
            entry=entry,
            stage="content",
            status=EntryTaskState.STATUS_PAUSED,
            attempt_count=3,
        )
        TranslationChunkCache.objects.create(
            entry=entry,
            stage="content",
            cache_key="a" * 64,
            translated_text="<p>stale</p>",
            tokens=10,
        )

        _process_feed_entries(
            self.feed,
            [
                {
                    "id": "g-update",
                    "link": "https://example.com/g-update",
                    "title": "Old Title",
                    "published_parsed": now.timetuple(),
                    "updated_parsed": None,
                    "summary": "<p>brand new body</p>",
                    "author": "a",
                }
            ],
        )

        state = EntryTaskState.objects.get(entry=entry, stage="content")
        self.assertEqual(state.status, EntryTaskState.STATUS_PENDING)
        self.assertEqual(state.attempt_count, 0)
        self.assertEqual(
            TranslationChunkCache.objects.filter(entry=entry, stage="content").count(),
            0,
        )


class ErrorClassificationAndFetchPersistenceTests(TestCase):
    def setUp(self):
        self.agent = OpenAIAgent.objects.create(
            name="Err Agent",
            api_key="test-key",
            model="test-model",
            max_tokens=8000,
            max_chunk_tokens=4000,
            rate_limit_rpm=0,
        )
        self.feed = Feed.objects.create(
            name="Err Feed",
            feed_url="https://example.com/err.xml",
            translator=self.agent,
            translate_content=True,
            fetch_article=True,
            max_posts=10,
        )
        self.client = MagicMock()
        self.client.with_options.return_value = self.client
        self.create = self.client.chat.completions.create
        init = patch.object(OpenAIAgent, "_init", return_value=self.client)
        init.start()
        self.addCleanup(init.stop)
        sleep = patch("core.tasks.utils.time.sleep")
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)

    def test_finish_reason_length_is_truncated_error_and_not_retried(self):
        entry = Entry.objects.create(
            feed=self.feed,
            guid="trunc-1",
            link="https://example.com/trunc-1",
            original_title="Trunc",
            original_content="<p>Hello world</p>",
            pubdate=timezone.now(),
        )
        self.feed.fetch_article = False
        self.feed.save(update_fields=["fetch_article"])
        self.create.return_value = _mock_response(
            "<p>Cut off", tokens=88, reason="length"
        )

        handle_feeds_translation([self.feed], target_field="content")

        # Must NOT retry 3x with the same parameters!
        self.assertEqual(self.create.call_count, 1)
        entry.refresh_from_db()
        self.feed.refresh_from_db()
        self.assertIsNone(entry.translated_content)
        self.assertEqual(self.feed.total_tokens, 88)
        state = EntryTaskState.objects.get(entry=entry, stage="content")
        self.assertEqual(state.last_error_type, "truncated")

    def test_429_and_5xx_are_classified_and_retried(self):
        self.feed.fetch_article = False
        self.feed.save(update_fields=["fetch_article"])
        req = httpx.Request("POST", "https://api.example.com")

        for idx, (exc, expected_type) in enumerate(
            [
                (
                    RateLimitError("429", response=httpx.Response(429, request=req), body=None),
                    "rate_limit",
                ),
                (
                    InternalServerError(
                        "502", response=httpx.Response(502, request=req), body=None
                    ),
                    "server_error",
                ),
            ]
        ):
            with self.subTest(expected_type=expected_type):
                self.create.reset_mock(side_effect=True)
                self.create.side_effect = exc
                entry = Entry.objects.create(
                    feed=self.feed,
                    guid=f"err-{idx}",
                    link=f"https://example.com/err-{idx}",
                    original_title=f"Err {idx}",
                    original_content="<p>text</p>",
                    pubdate=timezone.now() - timedelta(minutes=idx),
                )
                handle_feeds_translation([self.feed], target_field="content")
                self.assertEqual(self.create.call_count, 3)
                state = EntryTaskState.objects.get(entry=entry, stage="content")
                self.assertEqual(state.last_error_type, expected_type)

    def test_fetch_article_persists_before_translation_and_is_not_refetched_on_retry(self):
        entry = Entry.objects.create(
            feed=self.feed,
            guid="fetch-1",
            link="https://example.com/fetch-1",
            original_title="Fetch",
            original_content="short rss summary",
            pubdate=timezone.now(),
        )
        self.create.side_effect = _timeout_error()

        with patch(
            "core.tasks.translate_feeds._fetch_article_content",
            return_value="<p>Full fetched article body</p>",
        ) as fetcher:
            handle_feeds_translation([self.feed], target_field="content")
            self.assertEqual(fetcher.call_count, 1)

            # Even though translation timed out, fetched body was persisted!
            entry.refresh_from_db()
            self.assertEqual(entry.original_content, "<p>Full fetched article body</p>")

            # Expire backoff and retry: must NOT re-fetch the web page.
            EntryTaskState.objects.filter(entry=entry, stage="content").update(
                next_retry_at=timezone.now() - timedelta(seconds=1)
            )
            self.create.side_effect = None
            self.create.return_value = _mock_response("<p>完整译文</p>", tokens=15)
            handle_feeds_translation([self.feed], target_field="content")
            self.assertEqual(fetcher.call_count, 1)

        entry.refresh_from_db()
        self.assertEqual(entry.translated_content, "<p>完整译文</p>")

    def test_force_update_does_not_refetch_already_fetched_article_or_wipe_chunks(self):
        html = _make_long_html(paragraphs=12)
        self.agent.max_chunk_tokens = 200
        self.agent.save(update_fields=["max_chunk_tokens"])
        entry = Entry.objects.create(
            feed=self.feed,
            guid="fetch-force-1",
            link="https://example.com/fetch-force-1",
            original_title="Fetch Force",
            original_content="short rss summary",
            pubdate=timezone.now(),
        )
        self.create.side_effect = [
            _mock_response("<p>CHUNK_1_CACHED</p>", tokens=10),
            _timeout_error(),
        ]
        req = RequestFactory().post("/core/feed/")
        req.user = User.objects.create_superuser("fu_admin", "fu@example.com", "pw")

        with patch(
            "core.tasks.translate_feeds._fetch_article_content",
            side_effect=[html, html + "<p>dynamic footer</p>"],
        ) as fetcher:
            handle_feeds_translation([self.feed], target_field="content")
            self.assertEqual(fetcher.call_count, 1)
            self.assertEqual(
                TranslationChunkCache.objects.filter(
                    entry=entry, stage="content"
                ).count(),
                1,
            )

            # Force-update resets attempt_count to 0, yet must NOT re-fetch the
            # web page or wipe the cached chunk.
            with patch("core.actions.task_manager.submit_task"):
                feed_force_update(None, req, Feed.objects.filter(pk=self.feed.pk))

            self.create.reset_mock(side_effect=True)
            self.create.return_value = _mock_response("<p>CHUNK_REST</p>", tokens=8)
            handle_feeds_translation([self.feed], target_field="content")
            self.assertEqual(fetcher.call_count, 1)

        entry.refresh_from_db()
        self.assertIn("<p>CHUNK_1_CACHED</p>", entry.translated_content)


class EntryTaskAdminAndCleanupTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_superuser("admin", "admin@example.com", "pw")
        self.agent = OpenAIAgent.objects.create(
            name="Admin Agent",
            api_key="k",
            model="m",
            max_tokens=8000,
        )
        self.feed = Feed.objects.create(
            name="Admin Feed",
            feed_url="https://example.com/admin.xml",
            translator=self.agent,
            translate_content=True,
        )
        self.entry = Entry.objects.create(
            feed=self.feed,
            guid="adm-1",
            link="https://example.com/adm-1",
            original_title="Adm",
            original_content="<p>hello</p>",
            pubdate=timezone.now(),
        )
        self.state = EntryTaskState.objects.create(
            entry=self.entry,
            stage="content",
            status=EntryTaskState.STATUS_FAILED,
            attempt_count=2,
            last_error_type="timeout",
            last_error="Request timed out.",
            next_retry_at=timezone.now() + timedelta(hours=2),
        )
        self.modeladmin = EntryTaskStateAdmin(EntryTaskState, core_admin_site)

    def _request(self):
        req = self.factory.post("/core/entrytaskstate/")
        req.user = self.user
        setattr(req, "session", "session")
        setattr(req, "_messages", FallbackStorage(req))
        return req

    def test_pause_resume_and_force_update_respect_manual_pause(self):
        req = self._request()
        qs = EntryTaskState.objects.filter(pk=self.state.pk)

        pause_entry_tasks(self.modeladmin, req, qs)
        self.state.refresh_from_db()
        self.assertEqual(self.state.status, EntryTaskState.STATUS_PAUSED)
        self.assertTrue(self.state.paused_manually)

        # Force update on feed must NOT unpause manually paused entries.
        with patch("core.actions.task_manager.submit_task"):
            feed_force_update(
                self.modeladmin, req, Feed.objects.filter(pk=self.feed.pk)
            )
        self.state.refresh_from_db()
        self.assertEqual(self.state.status, EntryTaskState.STATUS_PAUSED)
        self.assertTrue(self.state.paused_manually)

        # Resume action resets budget and clears manual pause.
        resume_entry_tasks(self.modeladmin, req, qs)
        self.state.refresh_from_db()
        self.assertEqual(self.state.status, EntryTaskState.STATUS_PENDING)
        self.assertEqual(self.state.attempt_count, 0)
        self.assertFalse(self.state.paused_manually)
        self.assertIsNone(self.state.next_retry_at)

    def test_retry_entry_tasks_now_targets_single_entry(self):
        other_entry = Entry.objects.create(
            feed=self.feed,
            guid="adm-2",
            link="https://example.com/adm-2",
            original_title="Other",
            original_content="<p>other</p>",
            pubdate=timezone.now() - timedelta(minutes=1),
        )
        req = self._request()
        qs = EntryTaskState.objects.filter(pk=self.state.pk)

        with (
            patch("core.admin.entry_admin.task_manager.submit_task") as submit,
            self.captureOnCommitCallbacks(execute=True),
        ):
            retry_entry_tasks_now(self.modeladmin, req, qs)
        submit.assert_called_once()
        _, fn, feed_id, stage_entries = submit.call_args.args
        self.assertIs(fn, retry_feed_entry_stages)
        self.assertEqual(feed_id, self.feed.pk)
        self.assertEqual(stage_entries, {"content": [self.entry.pk]})

        # Execute the worker directly and verify ONLY self.entry is translated.
        with patch.object(
            OpenAIAgent,
            "translate",
            return_value={"text": "<p>你好</p>", "tokens": 5, "characters": 0},
        ) as mock_translate:
            self.assertTrue(retry_feed_entry_stages(feed_id, stage_entries))
        mock_translate.assert_called_once()
        self.entry.refresh_from_db()
        other_entry.refresh_from_db()
        self.assertEqual(self.entry.translated_content, "<p>你好</p>")
        self.assertIsNone(other_entry.translated_content)

    def test_clean_translated_content_and_cleanup_entries_prune_chunk_cache(self):
        req = self._request()
        expired = TranslationChunkCache.objects.create(
            entry=self.entry,
            stage="content",
            cache_key="b" * 64,
            translated_text="old",
            tokens=1,
        )
        TranslationChunkCache.objects.filter(pk=expired.pk).update(
            created_at=timezone.now() - timedelta(days=10)
        )
        cleanup_all_feeds()
        self.assertFalse(TranslationChunkCache.objects.filter(pk=expired.pk).exists())

        TranslationChunkCache.objects.create(
            entry=self.entry,
            stage="content",
            cache_key="c" * 64,
            translated_text="fresh",
            tokens=1,
        )
        clean_translated_content(
            self.modeladmin, req, Feed.objects.filter(pk=self.feed.pk)
        )
        self.assertFalse(
            EntryTaskState.objects.filter(entry=self.entry, stage="content").exists()
        )
        self.assertFalse(
            TranslationChunkCache.objects.filter(entry=self.entry, stage="content").exists()
        )

    def test_retry_single_entry_stage_summary_partial_and_noop_cache_bump(self):
        tag = Tag.objects.create(name="sum-tag")
        self.feed.summarizer = self.agent
        self.feed.summary = True
        self.feed.save(update_fields=["summarizer", "summary"])
        self.feed.tags.add(tag)

        done_entry = Entry.objects.create(
            feed=self.feed,
            guid="sum-done",
            link="https://example.com/sum-done",
            original_title="Done",
            original_content="<p>done</p>",
            ai_summary="already summarized",
            pubdate=timezone.now(),
        )
        # Already summarized entry -> no-op, must not bump caches.
        with patch("core.admin.entry_admin.bump_cache_version") as bump:
            self.assertFalse(
                retry_single_entry_stage(
                    self.feed.pk, [done_entry.pk], EntryTaskState.STAGE_SUMMARY
                )
            )
        bump.assert_not_called()

        # Two pending entries: first succeeds, second fails -> partial success
        # must STILL return True and bump rss/tag caches.
        fail_entry = Entry.objects.create(
            feed=self.feed,
            guid="sum-fail",
            link="https://example.com/sum-fail",
            original_title="Fail",
            original_content="<p>fail</p>",
            pubdate=timezone.now() - timedelta(minutes=1),
        )
        with (
            patch.object(
                OpenAIAgent,
                "summarize",
                side_effect=[
                    {"text": "Good summary", "tokens": 5},
                    _timeout_error(),
                ],
            ),
            patch("core.admin.entry_admin.bump_cache_version") as bump,
        ):
            result = retry_single_entry_stage(
                self.feed.pk,
                [self.entry.pk, fail_entry.pk],
                EntryTaskState.STAGE_SUMMARY,
            )
        self.assertTrue(result)
        self.entry.refresh_from_db()
        fail_entry.refresh_from_db()
        self.assertEqual(self.entry.ai_summary, "Good summary")
        self.assertIsNone(fail_entry.ai_summary)
        calls = [c.args for c in bump.call_args_list]
        self.assertIn(("rss", self.feed.pk), calls)
        self.assertIn(("tag", tag.pk), calls)

    def test_retry_single_entry_stage_skips_when_feed_lock_is_held(self):
        with (
            patch(
                "core.admin.entry_admin._try_acquire_feed_lock", return_value=None
            ),
            patch("core.admin.entry_admin.translate_feed") as mock_translate,
        ):
            self.assertFalse(
                retry_single_entry_stage(
                    self.feed.pk, [self.entry.pk], EntryTaskState.STAGE_CONTENT
                )
            )
        mock_translate.assert_not_called()

    def test_feed_admin_language_and_translator_changes_clean_or_reset_task_states(self):
        feed_admin = FeedAdmin(Feed, core_admin_site)
        req = self._request()

        # Auto-paused content state, manually paused title state, and auto-paused summary state.
        content_state = self.state
        content_state.status = EntryTaskState.STATUS_PAUSED
        content_state.paused_manually = False
        content_state.save()
        title_state = EntryTaskState.objects.create(
            entry=self.entry,
            stage=EntryTaskState.STAGE_TITLE,
            status=EntryTaskState.STATUS_PAUSED,
            attempt_count=3,
            paused_manually=True,
        )
        summary_state = EntryTaskState.objects.create(
            entry=self.entry,
            stage=EntryTaskState.STAGE_SUMMARY,
            status=EntryTaskState.STATUS_PAUSED,
            attempt_count=3,
            paused_manually=False,
        )
        TranslationChunkCache.objects.create(
            entry=self.entry,
            stage=EntryTaskState.STAGE_CONTENT,
            cache_key="d" * 64,
            translated_text="cached",
            tokens=2,
        )

        # Changing translator_option resets auto-paused title/content only
        # and clears stale title/content chunk caches.
        form = MagicMock()
        form.changed_data = ["translator_option"]
        with patch.object(feed_admin, "_submit_feed_update_task"):
            feed_admin.save_model(req, self.feed, form, True)
        content_state.refresh_from_db()
        title_state.refresh_from_db()
        summary_state.refresh_from_db()
        self.assertEqual(content_state.status, EntryTaskState.STATUS_PENDING)
        self.assertEqual(content_state.attempt_count, 0)
        self.assertEqual(title_state.status, EntryTaskState.STATUS_PAUSED)
        self.assertEqual(summary_state.status, EntryTaskState.STATUS_PAUSED)
        self.assertFalse(
            TranslationChunkCache.objects.filter(
                entry=self.entry, stage=EntryTaskState.STAGE_CONTENT
            ).exists()
        )

        # Re-enabling summary resets auto-paused summary task state too.
        self.feed.summary = True
        form.changed_data = ["summary"]
        with patch.object(feed_admin, "_submit_feed_update_task"):
            feed_admin.save_model(req, self.feed, form, True)
        summary_state.refresh_from_db()
        self.assertEqual(summary_state.status, EntryTaskState.STATUS_PENDING)

        # Changing target_language wipes translations and deletes all states & chunk caches.
        TranslationChunkCache.objects.create(
            entry=self.entry,
            stage=EntryTaskState.STAGE_CONTENT,
            cache_key="e" * 64,
            translated_text="cached2",
            tokens=2,
        )
        form.changed_data = ["target_language"]
        with patch.object(feed_admin, "_submit_feed_update_task"):
            feed_admin.save_model(req, self.feed, form, True)
        self.assertFalse(EntryTaskState.objects.filter(entry=self.entry).exists())
        self.assertFalse(
            TranslationChunkCache.objects.filter(entry=self.entry).exists()
        )


class FirstPrinciplesReviewRegressionTests(TestCase):
    """Regressions verifying the invariants uncovered in first-principles review."""

    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_superuser("fp_admin", "fp@example.com", "pw")
        self.agent = OpenAIAgent.objects.create(
            name="FP Agent",
            api_key="test-key",
            model="gemini-large",
            max_tokens=998540,
            max_chunk_tokens=4000,
            rate_limit_rpm=0,
        )
        self.feed = Feed.objects.create(
            name="FP Feed",
            feed_url="https://example.com/fp.xml",
            translator=self.agent,
            summarizer=self.agent,
            translate_title=True,
            translate_content=True,
            summary=True,
            fetch_article=True,
            max_posts=10,
        )
        self.client = MagicMock()
        self.client.with_options.return_value = self.client
        self.create = self.client.chat.completions.create
        init = patch.object(OpenAIAgent, "_init", return_value=self.client)
        init.start()
        self.addCleanup(init.stop)
        sleep = patch("core.tasks.utils.time.sleep")
        sleep.start()
        self.addCleanup(sleep.stop)

    def _request(self):
        req = self.factory.post("/core/entrytaskstate/")
        req.user = self.user
        setattr(req, "session", "session")
        setattr(req, "_messages", FallbackStorage(req))
        return req

    def test_retry_multi_stage_same_feed_batches_under_single_lock_and_restores_status(self):
        """P1: Retrying title+content+summary for the same feed submits ONE
        task per feed, runs causally (title -> content -> summary) under a
        single flock, updates last_translate, and recovers feed.translation_status."""
        self.feed.translation_status = False
        self.feed.save(update_fields=["translation_status"])
        entry = Entry.objects.create(
            feed=self.feed,
            guid="multi-stage-1",
            link="https://example.com/multi-stage-1",
            original_title="Original Title",
            original_content="<p>Short RSS summary</p>",
            pubdate=timezone.now(),
        )
        t_state = EntryTaskState.objects.create(
            entry=entry, stage="title", status=EntryTaskState.STATUS_FAILED, attempt_count=1
        )
        c_state = EntryTaskState.objects.create(
            entry=entry, stage="content", status=EntryTaskState.STATUS_FAILED, attempt_count=1
        )
        s_state = EntryTaskState.objects.create(
            entry=entry, stage="summary", status=EntryTaskState.STATUS_FAILED, attempt_count=1
        )
        modeladmin = EntryTaskStateAdmin(EntryTaskState, core_admin_site)
        qs = EntryTaskState.objects.filter(pk__in=[t_state.pk, c_state.pk, s_state.pk])

        with (
            patch("core.admin.entry_admin.task_manager.submit_task") as submit,
            self.captureOnCommitCallbacks(execute=True),
        ):
            retry_entry_tasks_now(modeladmin, self._request(), qs)

        # Must submit exactly ONE per-feed task instead of 3 competing locks!
        submit.assert_called_once()
        _, fn, feed_id, stage_entries = submit.call_args.args
        self.assertIs(fn, retry_feed_entry_stages)
        self.assertEqual(feed_id, self.feed.pk)
        self.assertEqual(
            list(stage_entries.keys()),
            ["title", "content", "summary"],
        )

        # Execute with real flock and verify causal order: summary sees the
        # full article fetched by the content stage!
        seen_summary_input = []

        def api_reply(**kwargs):
            user_msg = kwargs["messages"][-1]["content"]
            if "Original Title" in user_msg:
                return _mock_response("中文标题", tokens=5)
            if "Full fetched article" in user_msg and "<p>" in user_msg:
                return _mock_response("<p>完整中文正文</p>", tokens=15)
            seen_summary_input.append(user_msg)
            return _mock_response("完整摘要", tokens=10)

        self.create.side_effect = api_reply
        with patch(
            "core.tasks.translate_feeds._fetch_article_content",
            return_value="<p>Full fetched article body</p>",
        ):
            self.assertTrue(retry_feed_entry_stages(feed_id, stage_entries))

        entry.refresh_from_db()
        self.feed.refresh_from_db()
        self.assertEqual(entry.translated_title, "中文标题")
        self.assertEqual(entry.translated_content, "<p>完整中文正文</p>")
        self.assertEqual(entry.ai_summary, "完整摘要")
        self.assertTrue(entry.article_fetched)
        self.assertTrue(any("Full fetched article body" in s for s in seen_summary_input))
        self.assertTrue(self.feed.translation_status)
        self.assertIsNotNone(self.feed.last_translate)

    def test_fetch_article_retries_after_web_fetch_failure_and_when_enabled_later(self):
        """P1: Entry.article_fetched decouples data state from retry counters."""
        # Case 1: first web fetch fails ("") AND translation times out ->
        # article_fetched remains False so retry still fetches the full article.
        entry = Entry.objects.create(
            feed=self.feed,
            guid="fetch-retry-1",
            link="https://example.com/fetch-retry-1",
            original_title="Fetch Retry",
            original_content="<p>rss blurb</p>",
            source_hash=_source_hash("<p>rss blurb</p>"),
            pubdate=timezone.now(),
        )
        self.create.side_effect = _timeout_error()
        with patch(
            "core.tasks.translate_feeds._fetch_article_content",
            side_effect=["", "<p>Full body on attempt 2</p>"],
        ) as fetcher:
            handle_feeds_translation([self.feed], target_field="content")
            entry.refresh_from_db()
            self.assertFalse(entry.article_fetched)
            self.assertEqual(entry.original_content, "<p>rss blurb</p>")

            EntryTaskState.objects.filter(entry=entry, stage="content").update(
                next_retry_at=timezone.now() - timedelta(seconds=1)
            )
            self.create.side_effect = None
            self.create.return_value = _mock_response("<p>完整译文</p>", tokens=12)
            handle_feeds_translation([self.feed], target_field="content")
            self.assertEqual(fetcher.call_count, 2)

        entry.refresh_from_db()
        self.assertTrue(entry.article_fetched)
        self.assertEqual(entry.original_content, "<p>Full body on attempt 2</p>")
        self.assertEqual(entry.translated_content, "<p>完整译文</p>")

        # Case 2: RSS payload updates -> article_fetched resets to False.
        now = timezone.now()
        _process_feed_entries(
            self.feed,
            [
                {
                    "id": "fetch-retry-1",
                    "link": "https://example.com/fetch-retry-1",
                    "title": "Fetch Retry",
                    "published_parsed": now.timetuple(),
                    "updated_parsed": None,
                    "summary": "<p>new rss blurb v2</p>",
                    "author": "a",
                }
            ],
        )
        entry.refresh_from_db()
        self.assertFalse(entry.article_fetched)

        # Case 3: fetch_article was False when entry failed, then enabled later.
        self.feed.fetch_article = False
        self.feed.save(update_fields=["fetch_article"])
        entry2 = Entry.objects.create(
            feed=self.feed,
            guid="fetch-later",
            link="https://example.com/fetch-later",
            original_title="Later",
            original_content="<p>rss summary only</p>",
            pubdate=now - timedelta(minutes=1),
        )
        self.create.side_effect = _timeout_error()
        handle_feeds_translation([self.feed], target_field="content")

        self.feed.fetch_article = True
        self.feed.save(update_fields=["fetch_article"])
        EntryTaskState.objects.filter(entry=entry2, stage="content").update(
            next_retry_at=timezone.now() - timedelta(seconds=1)
        )
        self.create.side_effect = None
        self.create.return_value = _mock_response("<p>后续全文译文</p>", tokens=12)
        with patch(
            "core.tasks.translate_feeds._fetch_article_content",
            return_value="<p>Fetched after enabling</p>",
        ) as fetcher:
            handle_feeds_translation([self.feed], target_field="content")
            fetcher.assert_called_once()
        entry2.refresh_from_db()
        self.assertTrue(entry2.article_fetched)
        self.assertEqual(entry2.original_content, "<p>Fetched after enabling</p>")

    def test_large_context_summarizer_respects_max_chunk_tokens_and_caches_chunks(self):
        """P1: 1M-context OpenAIAgent sizes summary chunks by max_chunk_tokens
        (enabling recursive summary context) and resumes completed summary chunks
        from TranslationChunkCache without double billing."""
        self.agent.max_tokens = 998540
        self.agent.max_chunk_tokens = 250
        self.agent.save(update_fields=["max_tokens", "max_chunk_tokens"])

        self.assertEqual(self.agent.min_size(), 250 * 0.7)
        self.assertEqual(self.agent.max_size(), 250 * 0.9)

        self.feed.summary_detail = 1.0
        self.feed.save(update_fields=["summary_detail"])
        long_article = "\n".join(
            f"<p>Section {i}: Constitutional interpretation requires careful "
            f"analysis of text, history, structure, and precedent across courts.</p>"
            for i in range(40)
        )
        entry = Entry.objects.create(
            feed=self.feed,
            guid="sum-resume-1",
            link="https://example.com/sum-resume-1",
            original_title="Long Summary",
            original_content=long_article,
            pubdate=timezone.now(),
        )

        # Run 1: chunk 1 succeeds (15 tokens), chunk 2 times out.
        self.create.side_effect = [
            _mock_response("Summary part 1.", tokens=15),
            _timeout_error(),
        ]
        handle_feeds_summary([self.feed])
        self.assertEqual(self.create.call_count, 2)
        self.feed.refresh_from_db()
        self.assertEqual(self.feed.total_tokens, 15)
        cached_rows = list(
            TranslationChunkCache.objects.filter(entry=entry, stage="summary")
        )
        self.assertEqual(len(cached_rows), 1)
        # Billed atomically on failure -> unbilled balance zeroed.
        self.assertEqual(cached_rows[0].tokens, 0)

        # Run 2: expire backoff -> chunk 1 reused from DB cache, remaining run.
        EntryTaskState.objects.filter(entry=entry, stage="summary").update(
            next_retry_at=timezone.now() - timedelta(seconds=1)
        )
        self.create.reset_mock(side_effect=True)
        calls = {"n": 0}

        def finish_summary(**kwargs):
            calls["n"] += 1
            return _mock_response(f"Summary part {calls['n'] + 1}.", tokens=10)

        self.create.side_effect = finish_summary
        handle_feeds_summary([self.feed])

        entry.refresh_from_db()
        self.feed.refresh_from_db()
        self.assertIn("Summary part 1.", entry.ai_summary)
        self.assertIn("Summary part 2.", entry.ai_summary)
        # No double billing for chunk 1!
        self.assertEqual(self.feed.total_tokens, 15 + 10 * calls["n"])
        self.assertEqual(
            TranslationChunkCache.objects.filter(entry=entry, stage="summary").count(),
            0,
        )

    def test_output_token_limit_respects_context_conservation_and_truncation_recovery(self):
        """P2: input + max_completion_tokens never exceeds max_tokens;
        short truncation pauses immediately; long truncation adaptively splits."""
        # Conservation on tight context window (max_chunk_tokens=0).
        tight = OpenAIAgent.objects.create(
            name="Tight 4k",
            api_key="k",
            model="m",
            max_tokens=4096,
            max_chunk_tokens=0,
            rate_limit_rpm=0,
        )
        self.create.return_value = _mock_response("ok", tokens=10)
        long_prompt_text = "word " * 3700
        tight.completions(long_prompt_text, system_prompt="Translate this.")
        call_kwargs = self.create.call_args.kwargs
        sent_input = get_token_count("Translate this.") + get_token_count(long_prompt_text)
        self.assertLessEqual(
            sent_input + call_kwargs["max_completion_tokens"],
            tight.max_tokens,
        )

        # Short text finish_reason=length pauses immediately (no 3x cross-round burn).
        self.feed.fetch_article = False
        self.feed.save(update_fields=["fetch_article"])
        short_entry = Entry.objects.create(
            feed=self.feed,
            guid="trunc-short",
            link="https://example.com/trunc-short",
            original_title="Short",
            original_content="<p>Hello world</p>",
            pubdate=timezone.now(),
        )
        self.create.reset_mock(side_effect=True)
        self.create.return_value = _mock_response("<p>cut", tokens=50, reason="length")
        handle_feeds_translation([self.feed], target_field="content")
        self.assertEqual(self.create.call_count, 1)
        state = EntryTaskState.objects.get(entry=short_entry, stage="content")
        self.assertEqual(state.status, EntryTaskState.STATUS_PAUSED)
        self.assertIsNone(state.next_retry_at)
        # Next run makes 0 API calls.
        handle_feeds_translation([self.feed], target_field="content")
        self.assertEqual(self.create.call_count, 1)

        # Long text finish_reason=length adaptively splits in half and recovers
        # while accounting for both the truncated attempt and sub-chunks.
        big_html = _make_long_html(paragraphs=25)  # > 500 tokens, < 4000 budget
        self.assertGreater(get_token_count(big_html), 500)
        self.assertLess(get_token_count(big_html), 4000)
        self.create.reset_mock(side_effect=True)
        call_idx = {"n": 0}

        def truncate_then_succeed(**kwargs):
            call_idx["n"] += 1
            if call_idx["n"] == 1:
                return _mock_response("<p>partial", tokens=80, reason="length")
            return _mock_response(f"<p>Sub {call_idx['n']}</p>", tokens=20)

        self.create.side_effect = truncate_then_succeed
        res = self.agent.translate(
            big_html, target_language="Chinese", text_type="content"
        )
        self.assertGreaterEqual(call_idx["n"], 3)  # 1 truncated + >=2 sub-chunks
        self.assertEqual(res["tokens"], 80 + 20 * (call_idx["n"] - 1))
        self.assertIn("<p>Sub 2</p>", res["text"])

    def test_backoff_skipped_entry_keeps_feed_translation_status_false(self):
        """P2: An unfinished entry in STATUS_FAILED (waiting backoff) must not
        flip feed.translation_status to True on intermediate runs."""
        self.feed.fetch_article = False
        self.feed.save(update_fields=["fetch_article"])
        entry = Entry.objects.create(
            feed=self.feed,
            guid="backoff-status",
            link="https://example.com/backoff-status",
            original_title="Backoff",
            original_content="<p>Body</p>",
            pubdate=timezone.now(),
        )
        self.create.side_effect = _timeout_error()

        # Content translation: run 1 fails, run 2 skips in backoff -> STILL False.
        handle_feeds_translation([self.feed], target_field="content")
        self.feed.refresh_from_db()
        self.assertFalse(self.feed.translation_status)

        handle_feeds_translation([self.feed], target_field="content")
        self.feed.refresh_from_db()
        self.assertEqual(self.create.call_count, 1)
        self.assertFalse(self.feed.translation_status)

        # Summary: run 1 fails, run 2 skips in backoff -> STILL False.
        handle_feeds_summary([self.feed])
        self.feed.refresh_from_db()
        self.assertFalse(self.feed.translation_status)

        handle_feeds_summary([self.feed])
        self.feed.refresh_from_db()
        self.assertEqual(self.create.call_count, 2)
        self.assertFalse(self.feed.translation_status)

        # Once paused (isolated), skipping no longer marks the feed failed.
        EntryTaskState.objects.filter(entry=entry, stage="content").update(
            status=EntryTaskState.STATUS_PAUSED, next_retry_at=None
        )
        handle_feeds_translation([self.feed], target_field="content")
        self.feed.refresh_from_db()
        self.assertTrue(self.feed.translation_status)

    def test_chunk_html_preserves_syntax_highlighted_code_entities_and_atomic_img(self):
        """P2: Syntax-highlighted <pre><code> preserves indentation, newlines,
        and HTML entities; atomic oversized <img> is never sliced on dots."""
        code_body = "\n".join(
            f'<span class="kw">if</span> a[{i}] &lt; b[{i}]:\n'
            f'    <span class="kw">return</span> a[{i}] &amp; b[{i}]'
            for i in range(25)
        )
        highlighted = f'<pre class="notranslate"><code>{code_body}\n</code></pre>'
        chunks = chunk_html(highlighted, max_tokens=140)
        self.assertGreater(len(chunks), 2)
        for chunk in chunks:
            self.assertLessEqual(get_token_count(chunk), 140)
            self.assertIn('<pre class="notranslate"><code>', chunk)
            self.assertIn("</code></pre>", chunk)
            # Indentation stays attached to its return line and entities stay escaped.
            self.assertNotIn("a[0] < b[0]", chunk)
            self.assertIn("    <span", chunk)

        # Short NavigableString next to an inline tag must remain HTML-escaped.
        inline_entities = (
            "<p>"
            + " ".join(f'<span>s{i}</span> &lt; v{i} &amp; w{i}.' for i in range(40))
            + "</p>"
        )
        ent_chunks = chunk_html(inline_entities, max_tokens=80)
        self.assertGreater(len(ent_chunks), 2)
        for chunk in ent_chunks:
            self.assertNotIn(" < v", chunk)
            self.assertIn("&lt; v", chunk)

        # Oversized atomic <img> (e.g. data URI / long srcset with dots) must
        # never be split through its tag syntax, and still completes in agent.
        expected_src = (
            "https://cdn.example.com/a.b.c/"
            + ".".join(f"seg{i}" for i in range(40))
            + ".png"
        )
        big_img = f'<img alt="diagram" src="{expected_src}"/>'
        doc = f"<p>Intro paragraph.</p>\n{big_img}\n<p>Outro paragraph.</p>"
        img_chunks = chunk_html(doc, max_tokens=60)
        parsed_imgs = [
            img
            for chunk in img_chunks
            for img in BeautifulSoup(chunk, "lxml").find_all("img")
        ]
        self.assertEqual(len(parsed_imgs), 1)
        self.assertEqual(parsed_imgs[0].get("src"), expected_src)
        self.assertEqual(parsed_imgs[0].get("alt"), "diagram")
        self.assertFalse(any('src="https://cdn.example."' in c for c in img_chunks))

        # Sending that doc through OpenAIAgent with small max_chunk_tokens but
        # sufficient max_tokens must not hit recursion depth on the atomic <img>.
        self.agent.max_chunk_tokens = 60
        self.agent.save(update_fields=["max_chunk_tokens"])
        self.create.return_value = _mock_response("<p>ok</p>", tokens=5)
        res = self.agent.translate(doc, target_language="Chinese", text_type="content")
        self.assertIn("<p>ok</p>", res["text"])

    def test_crash_recovery_bills_unbilled_cached_chunk_tokens_once(self):
        """P3: Double-entry accounting recovers tokens when a process dies
        after writing TranslationChunkCache but before record_spent_tokens()."""
        self.agent.max_chunk_tokens = 200
        self.agent.save(update_fields=["max_chunk_tokens"])
        self.feed.fetch_article = False
        self.feed.save(update_fields=["fetch_article"])

        html = _make_long_html(paragraphs=12)
        entry = Entry.objects.create(
            feed=self.feed,
            guid="crash-acct-1",
            link="https://example.com/crash-acct-1",
            original_title="Crash Acct",
            original_content=html,
            pubdate=timezone.now(),
        )

        # Simulate Run 1: chunk 1 succeeds (17 tokens), then hard crash (OOM/SIGKILL)
        # before record_spent_tokens can run -> feed.total_tokens stays 0 while
        # TranslationChunkCache still holds the unbilled balance of 17.
        self.create.side_effect = [
            _mock_response("<p>C1_CRASHED</p>", tokens=17),
            _timeout_error(),
        ]
        with patch("core.tasks.translate_feeds.record_spent_tokens"):
            handle_feeds_translation([self.feed], target_field="content")

        self.feed.refresh_from_db()
        self.assertEqual(self.feed.total_tokens, 0)
        cached = TranslationChunkCache.objects.get(entry=entry, stage="content")
        self.assertEqual(cached.tokens, 17)

        # Run 2 (after restart): reuses chunk 1 and finishes remaining chunks ->
        # recovers the 17 unbilled tokens from Run 1 + Run 2's API tokens!
        EntryTaskState.objects.filter(entry=entry, stage="content").update(
            next_retry_at=timezone.now() - timedelta(seconds=1)
        )
        self.create.reset_mock(side_effect=True)
        run2_calls = {"n": 0}

        def finish(**kwargs):
            run2_calls["n"] += 1
            return _mock_response(f"<p>R2_{run2_calls['n']}</p>", tokens=11)

        self.create.side_effect = finish
        handle_feeds_translation([self.feed], target_field="content")

        self.feed.refresh_from_db()
        self.assertEqual(self.feed.total_tokens, 17 + 11 * run2_calls["n"])
        self.assertEqual(
            TranslationChunkCache.objects.filter(entry=entry, stage="content").count(),
            0,
        )

    def test_persist_error_after_chunked_translation_does_not_double_bill_on_retry(self):
        """PersistError after all chunks succeed must carry billed_cache_keys so
        record_spent_tokens zeroes cached chunk balances and retries don't double-bill."""
        self.agent.max_chunk_tokens = 200
        self.agent.save(update_fields=["max_chunk_tokens"])
        self.feed.fetch_article = False
        self.feed.save(update_fields=["fetch_article"])

        html = _make_long_html(paragraphs=12)
        entry = Entry.objects.create(
            feed=self.feed,
            guid="persist-content-1",
            link="https://example.com/persist-content-1",
            original_title="Persist Content",
            original_content=html,
            pubdate=timezone.now(),
        )

        call_num = {"n": 0}

        def translate_chunk(**kwargs):
            call_num["n"] += 1
            return _mock_response(f"<p>TC_{call_num['n']}</p>", tokens=13)

        self.create.side_effect = translate_chunk
        real_save = Entry.save

        def fail_translated_save(obj, *args, **kwargs):
            if kwargs.get("update_fields") == ["translated_content"]:
                raise DatabaseError("transient lock")
            return real_save(obj, *args, **kwargs)

        with patch.object(Entry, "save", autospec=True, side_effect=fail_translated_save):
            handle_feeds_translation([self.feed], target_field="content")

        first_run_calls = call_num["n"]
        self.assertGreater(first_run_calls, 1)
        self.feed.refresh_from_db()
        self.assertEqual(self.feed.total_tokens, first_run_calls * 13)
        # Cached chunks remain persisted for reuse, with unbilled balance zeroed.
        cached_rows = list(
            TranslationChunkCache.objects.filter(entry=entry, stage="content")
        )
        self.assertEqual(len(cached_rows), first_run_calls)
        self.assertTrue(all(row.tokens == 0 for row in cached_rows))

        # Run 2: all chunks hit cache (0 provider calls) and total_tokens is unchanged.
        handle_feeds_translation([self.feed], target_field="content")
        self.assertEqual(call_num["n"], first_run_calls)
        self.feed.refresh_from_db()
        self.assertEqual(self.feed.total_tokens, first_run_calls * 13)
        entry.refresh_from_db()
        self.assertIn("<p>TC_1</p>", entry.translated_content)
        self.assertEqual(
            TranslationChunkCache.objects.filter(entry=entry, stage="content").count(),
            0,
        )

    def test_persist_error_after_chunked_summary_does_not_double_bill_on_retry(self):
        """PersistError during summarize_feed must zero cached summary chunk balances."""
        self.agent.max_chunk_tokens = 400
        self.agent.save(update_fields=["max_chunk_tokens"])
        self.feed.summary_detail = 1.0
        self.feed.save(update_fields=["summary_detail"])

        long_body = "<p>" + " ".join(
            f"Section {i} analyzes constitutional structure and federalism in detail."
            for i in range(120)
        ) + "</p>"
        entry = Entry.objects.create(
            feed=self.feed,
            guid="persist-summary-1",
            link="https://example.com/persist-summary-1",
            original_title="Persist Summary",
            original_content=long_body,
            pubdate=timezone.now(),
        )

        call_num = {"n": 0}

        def summarize_chunk(**kwargs):
            call_num["n"] += 1
            return _mock_response(f"Summary part {call_num['n']}.", tokens=15)

        self.create.side_effect = summarize_chunk
        with patch.object(
            Entry.objects, "bulk_update", side_effect=DatabaseError("transient lock")
        ):
            ok = summarize_feed(
                self.feed,
                min_chunk_size=100,
                max_chunk_size=180,
                max_context_tokens=200,
            )

        self.assertFalse(ok)
        first_run_calls = call_num["n"]
        self.assertGreater(first_run_calls, 1)
        self.feed.refresh_from_db()
        self.assertEqual(self.feed.total_tokens, first_run_calls * 15)
        cached_rows = list(
            TranslationChunkCache.objects.filter(entry=entry, stage="summary")
        )
        self.assertEqual(len(cached_rows), first_run_calls)
        self.assertTrue(all(row.tokens == 0 for row in cached_rows))

        # Run 2: every chunk is served from cache with 0 API calls and no double-billing.
        ok = summarize_feed(
            self.feed,
            min_chunk_size=100,
            max_chunk_size=180,
            max_context_tokens=200,
        )
        self.assertTrue(ok)
        self.assertEqual(call_num["n"], first_run_calls)
        self.feed.refresh_from_db()
        self.assertEqual(self.feed.total_tokens, first_run_calls * 15)
        entry.refresh_from_db()
        self.assertIn("Summary part 1.", entry.ai_summary)
        self.assertEqual(
            TranslationChunkCache.objects.filter(entry=entry, stage="summary").count(),
            0,
        )

    def test_persist_error_in_digest_summary_does_not_double_bill_on_retry(self):
        """PersistError inside generate_digests._ensure_entries_have_summaries
        must also zero cached chunk balances so subsequent retries don't double-bill."""
        self.agent.max_chunk_tokens = 250
        self.agent.save(update_fields=["max_chunk_tokens"])
        self.feed.summary_detail = 1.0
        self.feed.save(update_fields=["summary_detail"])

        tag = Tag.objects.create(name="DigestTag", slug="digest-tag")
        self.feed.tags.add(tag)
        digest = Digest.objects.create(
            name="Daily Digest",
            slug="daily-digest-persist",
            summarizer=self.agent,
            target_language="English",
            days_range=7,
        )
        digest.tags.add(tag)

        long_body = "<p>" + " ".join(
            f"Paragraph {i} examines monetary policy and institutional design."
            for i in range(120)
        ) + "</p>"
        entry = Entry.objects.create(
            feed=self.feed,
            guid="digest-persist-1",
            link="https://example.com/digest-persist-1",
            original_title="Digest Persist",
            original_content=long_body,
            pubdate=timezone.now(),
        )

        call_num = {"n": 0}

        def summarize_chunk(**kwargs):
            call_num["n"] += 1
            return _mock_response(f"Digest chunk {call_num['n']}.", tokens=19)

        self.create.side_effect = summarize_chunk
        real_save = Entry.save

        def fail_summary_save(obj, *args, **kwargs):
            if kwargs.get("update_fields") == ["ai_summary"]:
                raise DatabaseError("transient lock")
            return real_save(obj, *args, **kwargs)

        with patch.object(Entry, "save", autospec=True, side_effect=fail_summary_save):
            failed = _ensure_entries_have_summaries(digest)

        self.assertEqual(failed, [entry.pk])
        first_run_calls = call_num["n"]
        self.assertGreater(first_run_calls, 1)
        self.feed.refresh_from_db()
        self.assertEqual(self.feed.total_tokens, first_run_calls * 19)
        cached_rows = list(
            TranslationChunkCache.objects.filter(entry=entry, stage="summary")
        )
        self.assertEqual(len(cached_rows), first_run_calls)
        self.assertTrue(all(row.tokens == 0 for row in cached_rows))

        # Second run reuses all cached summary chunks without double-billing.
        failed = _ensure_entries_have_summaries(digest)
        self.assertEqual(failed, [])
        self.assertEqual(call_num["n"], first_run_calls)
        self.feed.refresh_from_db()
        self.assertEqual(self.feed.total_tokens, first_run_calls * 19)

    def test_title_and_timeout_logs_include_entry_stage_attempt_id_and_elapsed(self):
        """Title calls must populate entry/stage/attempt_id logs, timeout failures
        must log elapsed/error_type/attempt_id, and attempt_id must not perturb cache keys."""
        prev_disable = logging.root.manager.disable
        logging.disable(logging.NOTSET)
        self.addCleanup(logging.disable, prev_disable)

        self.feed.fetch_article = False
        self.feed.save(update_fields=["fetch_article"])
        entry = Entry.objects.create(
            feed=self.feed,
            guid="obs-1",
            link="https://example.com/obs-1",
            original_title="Observability Check",
            original_content="<p>Body for timeout observability.</p>",
            pubdate=timezone.now(),
        )

        self.create.return_value = _mock_response("可观测性检查", tokens=12)
        with self.assertLogs("core.models.agent", level="INFO") as title_logs:
            handle_feeds_translation([self.feed], target_field="title")
        joined_title = "\n".join(title_logs.output)
        self.assertIn(f"entry={entry.pk}", joined_title)
        self.assertIn("stage=title", joined_title)
        self.assertNotIn("attempt_id=None", joined_title)

        self.create.side_effect = _timeout_error()
        with (
            self.assertLogs("core.models.agent", level="WARNING") as agent_logs,
            self.assertLogs("core.tasks.translate_feeds", level="ERROR") as task_logs,
        ):
            handle_feeds_translation([self.feed], target_field="content")

        joined_agent = "\n".join(agent_logs.output)
        joined_task = "\n".join(task_logs.output)
        self.assertIn(f"entry={entry.pk}", joined_agent)
        self.assertIn("stage=content", joined_agent)
        self.assertIn("error_type=timeout", joined_agent)
        self.assertIn("elapsed=", joined_agent)
        self.assertIn("est_input_tokens=", joined_agent)
        self.assertNotIn("attempt_id=None", joined_agent)
        self.assertIn("attempt_id=", joined_task)
        # Extract attempt_id from task log and verify it matches agent warning log.
        attempt_token = [
            tok for tok in joined_task.split() if tok.startswith("attempt_id=")
        ][0]
        self.assertIn(attempt_token, joined_agent)

        # Changing attempt_id must not change the chunk cache key.
        key_a = self.agent._build_chunk_cache_key(
            "<p>Same chunk</p>",
            "prompt",
            {"entry_id": entry.pk, "stage": "content", "target_language": "Chinese", "attempt_id": "aaa"},
        )
        key_b = self.agent._build_chunk_cache_key(
            "<p>Same chunk</p>",
            "prompt",
            {"entry_id": entry.pk, "stage": "content", "target_language": "Chinese", "attempt_id": "bbb"},
        )
        self.assertEqual(key_a, key_b)

    def test_openai_agent_clean_validates_chunk_size_and_request_timeout(self):
        """OpenAIAgent.full_clean() rejects negative chunk size and non-positive/non-finite timeout."""
        self.agent.valid = True
        self.agent.max_chunk_tokens = 0
        self.agent.request_timeout = None
        self.agent.full_clean()

        self.agent.max_chunk_tokens = 4000
        self.agent.request_timeout = 30.5
        self.agent.full_clean()

        self.agent.max_chunk_tokens = -1
        with self.assertRaises(ValidationError) as ctx:
            self.agent.full_clean()
        self.assertIn("max_chunk_tokens", ctx.exception.message_dict)
        self.agent.max_chunk_tokens = 4000

        for bad_timeout in (0.0, -5.0, float("nan"), float("inf")):
            self.agent.request_timeout = bad_timeout
            with self.assertRaises(ValidationError) as ctx:
                self.agent.full_clean()
            self.assertIn("request_timeout", ctx.exception.message_dict)

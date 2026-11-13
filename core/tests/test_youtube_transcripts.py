from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch
import json
import subprocess
from uuid import uuid4

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from core.models import Entry, Feed, OpenAIAgent, YouTubeTranscript
from core.tasks.summarize_feeds import handle_feeds_summary
from core.tasks.translate_feeds import handle_feeds_translation
from core.tasks.youtube_transcripts import processing_content
from utils.youtube_transcripts import (
    CaptionError,
    classify_error,
    fetch_fallback,
    fetch_primary,
    fetch_transcript,
    parse_json3,
    render_transcript,
    run_provider,
    select_subtitle,
    validate_result,
    video_id,
    preferred_caption_language,
    select_primary_track,
)

VIDEO = "abcdefghijk"
URL = f"https://www.youtube.com/watch?v={VIDEO}"
RESULT = {
    "provider": "youtube-transcript-api",
    "language": "en",
    "generated": False,
    "segments": [
        {
            "text": "Actual spoken content, not the description.",
            "start": 2.5,
            "duration": 4.0,
        }
    ],
}


class ExtractionTests(SimpleTestCase):
    def test_video_urls(self):
        for url in (
            URL,
            f"https://youtu.be/{VIDEO}?t=2",
            f"https://m.youtube.com/watch?v={VIDEO}",
            f"https://youtube.com/shorts/{VIDEO}",
            f"https://youtube.com/live/{VIDEO}",
            f"https://www.youtube-nocookie.com/embed/{VIDEO}",
        ):
            with self.subTest(url=url):
                self.assertEqual(video_id(url), VIDEO)
        for url in (
            None,
            "",
            "https://youtube.com/@channel",
            "https://youtube.com/playlist?list=abc",
            "https://youtube.com/watch?v=bad",
            f"https://youtube.com.evil/watch?v={VIDEO}",
            f"https://youtube.com@evil.test/watch?v={VIDEO}",
            f"file://youtube.com/watch?v={VIDEO}",
            "https://[bad",
        ):
            with self.subTest(url=url):
                self.assertIsNone(video_id(url))

    def test_only_extraction_errors_use_enabled_fallback(self):
        for code in ("token_required", "invalid_captions", "extraction_failed"):
            with (
                self.subTest(code=code),
                patch(
                    "utils.youtube_transcripts.run_provider",
                    side_effect=[CaptionError(code), RESULT],
                ) as run,
            ):
                self.assertEqual(fetch_transcript(VIDEO, True), RESULT)
                self.assertEqual(
                    [call.args[1] for call in run.call_args_list],
                    ["primary", "fallback"],
                )
        for code in (
            "blocked",
            "rate_limited",
            "no_captions",
            "unavailable",
            "timeout",
            "network_error",
            "missing_dependency",
        ):
            with (
                self.subTest(code=code),
                patch(
                    "utils.youtube_transcripts.run_provider",
                    side_effect=CaptionError(code),
                ) as run,
            ):
                with self.assertRaises(CaptionError):
                    fetch_transcript(VIDEO, True)
                self.assertEqual(run.call_count, 1)

    def test_fallback_is_opt_in_and_primary_success_short_circuits(self):
        with patch(
            "utils.youtube_transcripts.run_provider",
            side_effect=CaptionError("token_required"),
        ) as run:
            with self.assertRaises(CaptionError):
                fetch_transcript(VIDEO)
            self.assertEqual(run.call_count, 1)
        with patch(
            "utils.youtube_transcripts.run_provider", return_value=RESULT
        ) as run:
            self.assertEqual(fetch_transcript(VIDEO, True), RESULT)
            self.assertEqual(run.call_count, 1)

    def test_primary_prefers_manual_source_language_without_translation(self):
        auto = Mock(is_generated=True, language_code="en")
        manual = Mock(is_generated=False, language_code="zh-Hans")
        manual.fetch.return_value.to_raw_data.return_value = RESULT["segments"]
        with patch("youtube_transcript_api.YouTubeTranscriptApi") as api:
            api.return_value.list.return_value = [auto, manual]
            result = fetch_primary(VIDEO)
        self.assertEqual(result["language"], "zh-Hans")
        self.assertFalse(result["generated"])
        auto.fetch.assert_not_called()
        manual.translate.assert_not_called()

    def test_default_audio_language_avoids_alphabetical_dub_selection(self):
        arabic = Mock(is_generated=True, language_code="ar")
        english = Mock(is_generated=True, language_code="en")
        metadata = {
            "defaultAudioTrackIndex": 1,
            "audioTracks": [{"audioTrackId": "ar.10"}, {"audioTrackId": "en-US.4"}],
        }
        language = preferred_caption_language(metadata)
        self.assertEqual(language, "en-US")
        self.assertIs(select_primary_track([arabic, english], language), english)
        self.assertIsNone(preferred_caption_language({}))
        with self.assertRaises(CaptionError):
            select_primary_track([arabic, english])
        chinese = Mock(is_generated=False, language_code="zh-Hans")
        self.assertIs(
            select_primary_track([arabic, english, chinese], language), chinese
        )

    def test_network_errors_do_not_trigger_extraction_fallback(self):
        from requests.exceptions import HTTPError, ProxyError, SSLError
        from youtube_transcript_api._errors import YouTubeRequestFailed

        for exc in (
            YouTubeRequestFailed(VIDEO, HTTPError("500 Server Error")),
            ProxyError("proxy"),
            SSLError("certificate"),
        ):
            self.assertEqual(classify_error(exc), "network_error")
        self.assertEqual(
            classify_error(YouTubeRequestFailed(VIDEO, HTTPError("403 Forbidden"))),
            "blocked",
        )

    def test_automatic_captions_work_without_manual_track(self):
        auto = Mock(is_generated=True, language_code="en")
        auto.fetch.return_value.to_raw_data.return_value = RESULT["segments"]
        with patch("youtube_transcript_api.YouTubeTranscriptApi") as api:
            api.return_value.list.return_value = [auto]
            self.assertTrue(fetch_primary(VIDEO)["generated"])

    def test_select_subtitle_skips_translations_and_live_chat(self):
        def fmt(url):
            return [{"ext": "json3", "url": url}]

        info = {
            "automatic_captions": {
                "zh": fmt("https://example.test/caption?tlang=zh"),
                "en-orig": fmt("https://example.test/original"),
                "live_chat": fmt("https://example.test/chat"),
            }
        }
        self.assertEqual(
            select_subtitle(info), ("en-orig", True, "https://example.test/original")
        )
        info["subtitles"] = {"fr": fmt("https://example.test/manual")}
        self.assertEqual(
            select_subtitle(info), ("fr", False, "https://example.test/manual")
        )
        with self.assertRaises(CaptionError):
            select_subtitle(
                {"automatic_captions": {"zh": fmt("https://example.test/?tlang=zh")}}
            )

    def test_fallback_preserves_known_language_and_rejects_ambiguous_dubs(self):
        info = {
            "automatic_captions": {
                "ar-orig": [{"ext": "json3", "url": "https://example.test/ar"}],
                "en-orig": [{"ext": "json3", "url": "https://example.test/en"}],
            }
        }
        with self.assertRaises(CaptionError):
            select_subtitle(info)
        self.assertEqual(select_subtitle(info, "en-US")[0], "en-orig")
        with patch(
            "utils.youtube_transcripts.run_provider",
            side_effect=[CaptionError("token_required", "en"), RESULT],
        ) as run:
            fetch_transcript(VIDEO, True)
        self.assertEqual(run.call_args.args, (VIDEO, "fallback", "en"))

    def test_json3_and_escaping(self):
        data = {
            "events": [
                {"wWinId": 1},
                {
                    "tStartMs": 2500,
                    "dDurationMs": 1000,
                    "segs": [{"utf8": "<script>"}, {"utf8": " & text"}],
                },
            ]
        }
        result = dict(RESULT, segments=parse_json3(json.dumps(data)))
        output = render_transcript(result, VIDEO)
        self.assertIn("&lt;script&gt; &amp; text", output)
        self.assertNotIn("<script>", output)
        self.assertIn("&amp;t=2", output)
        self.assertIn("0:02", output)

    def test_invalid_or_empty_captions_are_not_success(self):
        for segments in (
            [],
            [{"text": "  ", "start": 0, "duration": 1}],
            [{"text": "words", "start": float("nan"), "duration": 1}],
            [{"text": "words", "start": -1, "duration": 1}],
            [{"text": None, "start": 0, "duration": 1}],
        ):
            with self.subTest(segments=segments), self.assertRaises(CaptionError):
                validate_result(dict(RESULT, segments=segments))
        for data in ("not json", "{}", '{"events": null}'):
            with self.assertRaises(CaptionError):
                parse_json3(data)

    def test_fallback_downloads_only_selected_caption(self):
        with patch("yt_dlp.YoutubeDL") as cls:
            downloader = cls.return_value.__enter__.return_value
            downloader.extract_info.return_value = {
                "subtitles": {
                    "en": [{"ext": "json3", "url": "https://example.test/captions"}]
                }
            }
            downloader.urlopen.return_value.__enter__.return_value.read.return_value = (
                json.dumps(
                    {
                        "events": [
                            {
                                "tStartMs": 0,
                                "dDurationMs": 1000,
                                "segs": [{"utf8": "Words"}],
                            }
                        ]
                    }
                ).encode()
            )
            result = fetch_fallback(VIDEO)
        self.assertEqual(result["provider"], "yt-dlp")
        self.assertTrue(cls.call_args.args[0]["skip_download"])
        self.assertFalse(downloader.extract_info.call_args.kwargs["download"])
        downloader.urlopen.assert_called_once_with("https://example.test/captions")

    def test_subprocess_timeout_and_invalid_output(self):
        with patch(
            "utils.youtube_transcripts.subprocess.run",
            side_effect=subprocess.TimeoutExpired("caption", 90),
        ):
            with self.assertRaises(CaptionError) as caught:
                run_provider(VIDEO, "primary")
            self.assertEqual(caught.exception.code, "timeout")
        with patch(
            "utils.youtube_transcripts.subprocess.run",
            return_value=SimpleNamespace(returncode=1),
        ):
            with self.assertRaises(CaptionError):
                run_provider(VIDEO, "primary")

    def test_language_hint_crosses_subprocess_boundary(self):
        def subprocess_result(*args, **kwargs):
            kwargs["stdout"].write(json.dumps(RESULT).encode())
            return SimpleNamespace(returncode=0)

        with patch(
            "utils.youtube_transcripts.subprocess.run", side_effect=subprocess_result
        ) as run:
            self.assertEqual(run_provider(VIDEO, "fallback", "en"), RESULT)
        self.assertEqual(run.call_args.args[0][-3:], ["fallback", VIDEO, "en"])

    def test_caption_fetch_failure_retains_selected_language(self):
        track = Mock(is_generated=True, language_code="en")
        track.fetch.side_effect = type("PoTokenRequired", (Exception,), {})(
            "signed URL"
        )
        with patch("youtube_transcript_api.YouTubeTranscriptApi") as api:
            api.return_value.list.return_value = [track]
            with self.assertRaises(CaptionError) as caught:
                fetch_primary(VIDEO)
        self.assertEqual(caught.exception.code, "token_required")
        self.assertEqual(caught.exception.language, "en")

    def test_error_details_are_not_exposed(self):
        self.assertEqual(
            classify_error(type("IpBlocked", (Exception,), {})("secret URL")), "blocked"
        )
        self.assertEqual(
            classify_error(Exception("HTTP Error 429 signed-token=secret")),
            "rate_limited",
        )
        self.assertNotIn("secret", str(CaptionError("signed-token=secret")))


class CaptionCacheTests(TestCase):
    def setUp(self):
        self.feed = Feed.objects.create(
            name="YouTube",
            feed_url="https://example.test/rss",
            fetch_youtube_transcript=True,
            youtube_transcript_fallback=True,
        )
        self.entry = Entry.objects.create(
            feed=self.feed,
            link=URL,
            original_title="Video",
            original_content="RSS description",
            pubdate=timezone.now(),
        )
        self.fetch_patch = patch(
            "core.tasks.youtube_transcripts.fetch_transcript", return_value=RESULT
        )
        self.fetch = self.fetch_patch.start()
        self.addCleanup(self.fetch_patch.stop)

    def test_shared_cache_across_feeds_keeps_original_description(self):
        first = processing_content(self.entry, self.feed)
        other = Feed.objects.create(
            feed_url="https://example.test/another", fetch_youtube_transcript=True
        )
        entry = Entry.objects.create(feed=other, link=f"https://youtu.be/{VIDEO}")
        self.assertEqual(processing_content(entry, other), first)
        self.fetch.assert_called_once_with(VIDEO, True)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.original_content, "RSS description")
        self.assertEqual(YouTubeTranscript.objects.get().attempts, 1)

    def test_off_and_non_youtube_do_not_fetch(self):
        self.feed.fetch_youtube_transcript = False
        self.assertEqual(processing_content(self.entry, self.feed), "RSS description")
        self.feed.fetch_youtube_transcript = True
        self.entry.link = "https://example.test/article"
        self.assertEqual(processing_content(self.entry, self.feed), "RSS description")
        self.fetch.assert_not_called()

    def test_failed_attempt_is_backed_off_then_recovers_without_new_rss_entry(self):
        self.fetch.side_effect = CaptionError("token_required")
        for _ in range(2):
            with self.assertRaises(CaptionError):
                processing_content(self.entry, self.feed)
        self.assertEqual(self.fetch.call_count, 1)
        row = YouTubeTranscript.objects.get()
        self.assertGreater(row.retry_after, timezone.now())
        self.assertIsNone(row.lease_id)
        YouTubeTranscript.objects.update(
            retry_after=timezone.now() - timedelta(seconds=1)
        )
        self.fetch.side_effect = None
        self.assertIn(
            "Actual spoken content", processing_content(self.entry, self.feed)
        )
        self.assertEqual(self.fetch.call_count, 2)

    def test_missing_captions_retry_after_one_day(self):
        self.fetch.side_effect = CaptionError("no_captions")
        with self.assertRaises(CaptionError):
            processing_content(self.entry, self.feed)
        row = YouTubeTranscript.objects.get()
        self.assertGreater(row.retry_after, timezone.now() + timedelta(hours=23))

    def test_block_pauses_other_channels_but_cached_content_still_works(self):
        self.fetch.side_effect = CaptionError("blocked")
        with self.assertRaises(CaptionError):
            processing_content(self.entry, self.feed)
        self.entry.link = "https://youtu.be/12345678901"
        with self.assertRaises(CaptionError):
            processing_content(self.entry, self.feed)
        self.assertEqual(self.fetch.call_count, 1)
        YouTubeTranscript.objects.filter(pk="12345678901").update(result=RESULT)
        self.assertIn(
            "Actual spoken content", processing_content(self.entry, self.feed)
        )

    def test_active_lease_prevents_duplicate_fetch_and_expired_lease_recovers(self):
        YouTubeTranscript.objects.create(
            video_id=VIDEO,
            lease_id=uuid4(),
            lease_until=timezone.now() + timedelta(minutes=3),
        )
        with self.assertRaises(CaptionError):
            processing_content(self.entry, self.feed)
        self.fetch.assert_not_called()
        YouTubeTranscript.objects.update(
            lease_until=timezone.now() - timedelta(seconds=1)
        )
        self.assertIn(
            "Actual spoken content", processing_content(self.entry, self.feed)
        )

    def test_lost_lease_cannot_overwrite_new_owner(self):
        def replaced(*args):
            YouTubeTranscript.objects.filter(pk=VIDEO).update(
                lease_id=uuid4(), result=dict(RESULT, language="fr")
            )
            return RESULT

        self.fetch.side_effect = replaced
        with self.assertRaises(CaptionError):
            processing_content(self.entry, self.feed)
        self.assertEqual(YouTubeTranscript.objects.get().result["language"], "fr")

    def setup_agent(self):
        agent = OpenAIAgent.objects.create(
            name="Test", api_key="unused", max_tokens=4096, rate_limit_rpm=0
        )
        self.feed.summarizer = agent
        self.feed.translator = agent
        self.feed.summary = True
        self.feed.translate_content = True
        self.feed.fetch_article = True
        self.feed.save()
        return agent

    def test_summary_only_uses_captions_and_deduplicates_on_next_run(self):
        self.setup_agent()
        with patch.object(
            OpenAIAgent,
            "summarize",
            return_value={"text": "Caption summary", "tokens": 7},
        ) as summarize:
            handle_feeds_summary([self.feed])
            self.assertIn("Actual spoken content", summarize.call_args.kwargs["text"])
            self.assertNotIn("RSS description", summarize.call_args.kwargs["text"])
            handle_feeds_summary([self.feed])
            self.assertEqual(summarize.call_count, 1)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.ai_summary, "Caption summary")
        self.assertEqual(self.entry.original_content, "RSS description")
        self.assertEqual(self.entry.link, URL)

    def test_content_and_summary_share_cache_and_skip_article_scraper(self):
        self.setup_agent()
        with (
            patch.object(
                OpenAIAgent,
                "translate",
                return_value={"text": "Translated captions", "tokens": 3},
            ) as translate,
            patch.object(
                OpenAIAgent, "summarize", return_value={"text": "Summary", "tokens": 4}
            ),
            patch("core.tasks.translate_feeds._fetch_article_content") as article,
        ):
            handle_feeds_translation([self.feed], "content")
            handle_feeds_summary([self.feed])
        article.assert_not_called()
        self.assertIn("Actual spoken content", translate.call_args.kwargs["text"])
        self.assertEqual(self.fetch.call_count, 1)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.original_content, "RSS description")
        self.assertEqual(self.entry.translated_content, "Translated captions")

    def test_missing_caption_never_summarizes_or_translates_description(self):
        self.setup_agent()
        self.fetch.side_effect = CaptionError("no_captions")
        with (
            patch.object(OpenAIAgent, "translate") as translate,
            patch.object(OpenAIAgent, "summarize") as summarize,
        ):
            handle_feeds_translation([self.feed], "content")
            handle_feeds_summary([self.feed])
        translate.assert_not_called()
        summarize.assert_not_called()
        self.entry.refresh_from_db()
        self.feed.refresh_from_db()
        self.assertIsNone(self.entry.ai_summary)
        self.assertIsNone(self.entry.translated_content)
        self.assertFalse(self.feed.translation_status)
        self.assertIn("no_captions", self.feed.log)
        self.assertEqual(self.fetch.call_count, 1)

    def test_empty_rss_body_still_translates_captions(self):
        self.setup_agent()
        self.entry.original_content = ""
        self.entry.save()
        with patch.object(
            OpenAIAgent, "translate", return_value={"text": "Translated", "tokens": 1}
        ) as translate:
            handle_feeds_translation([self.feed], "content")
        self.assertEqual(translate.call_count, 1)

    def test_admin_mode_switch_clears_derived_content_only(self):
        from core.admin.feed_admin import FeedAdmin
        from django.contrib.admin import AdminSite

        self.entry.ai_summary = "Old summary"
        self.entry.translated_content = "Old content"
        self.entry.translated_title = "Keep title"
        self.entry.save()
        admin = FeedAdmin(Feed, AdminSite())
        with (
            self.captureOnCommitCallbacks(execute=True),
            patch.object(admin, "_submit_feed_update_task") as submit,
        ):
            admin.save_model(
                None,
                self.feed,
                SimpleNamespace(changed_data=["fetch_youtube_transcript"]),
                True,
            )
        submit.assert_called_once()
        self.entry.refresh_from_db()
        self.assertIsNone(self.entry.ai_summary)
        self.assertIsNone(self.entry.translated_content)
        self.assertEqual(self.entry.translated_title, "Keep title")
        self.assertEqual(self.entry.original_content, "RSS description")

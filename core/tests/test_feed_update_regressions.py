"""Regression coverage for stalled downloads and overlapping scheduled updates."""

import fcntl
import io
import socket
import tempfile
import threading
import time
from concurrent.futures import Future
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from core.management.commands import feed_updater as cmd
from core.tasks.fetch_feeds import fetch_feed, manual_fetch_feed


@override_settings(RSS_FETCH_TIMEOUT=0.1, RSS_FETCH_USER_AGENT="rssbox-test")
class FeedDownloadTimeoutTests(SimpleTestCase):
    def setUp(self):
        self.release = threading.Event()
        self.requests = []
        release, requests = self.release, self.requests

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append((self.path, self.headers.get("If-None-Match")))
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "/stall")
                    self.end_headers()
                elif self.path == "/stall":
                    release.wait(3)
                elif self.path == "/body":
                    self.send_response(200)
                    self.send_header("Content-Length", "1000")
                    self.end_headers()
                    self.wfile.write(b"<rss>")
                    self.wfile.flush()
                    release.wait(3)
                elif self.headers.get("If-None-Match") == '"test-etag"':
                    self.send_response(304)
                    self.end_headers()
                else:
                    body = (
                        b'<?xml version="1.0" encoding="UTF-8"?>'
                        b'<rss version="2.0"><channel><title>Test</title>'
                        b'<link>/</link><description>Test</description>'
                        b'<item><title>Entry</title><link>/entry</link></item>'
                        b'</channel></rss>'
                    )
                    self.send_response(200)
                    self.send_header("Content-Type", "application/rss+xml; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("ETag", '"test-etag"')
                    self.end_headers()
                    self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server_thread = threading.Thread(target=self.server.serve_forever)
        self.server_thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=5)

    def test_stalled_headers_body_and_redirect_are_bounded(self):
        original_timeout = socket.getdefaulttimeout()
        for path in ("/stall", "/body", "/redirect"):
            with self.subTest(path=path):
                start = time.monotonic()
                result = fetch_feed(self.base_url + path)
                self.assertLess(time.monotonic() - start, 2)
                self.assertFalse(result["update"])
                self.assertIsNotNone(result["error"])
        self.assertEqual(socket.getdefaulttimeout(), original_timeout)

    def test_fallback_download_uses_configured_timeout(self):
        start = time.monotonic()
        result = manual_fetch_feed(self.base_url + "/stall")
        self.assertLess(time.monotonic() - start, 2)
        self.assertFalse(result["update"])
        self.assertIsNotNone(result["error"])

    def test_etag_and_relative_links_are_preserved(self):
        result = fetch_feed(self.base_url + "/feed")
        self.assertTrue(result["update"])
        self.assertIsNone(result["error"])
        self.assertEqual(result["feed"].etag, '"test-etag"')
        self.assertEqual(result["feed"].entries[0].link, self.base_url + "/entry")
        result = fetch_feed(self.base_url + "/feed", etag='"test-etag"')
        self.assertFalse(result["update"])
        self.assertIsNone(result["error"])
        self.assertIsNone(result["feed"])
        self.assertEqual(self.requests[-1], ("/feed", '"test-etag"'))


class UpdateLockLifetimeTests(SimpleTestCase):
    def check_lock_lifetime(self, submission_error=False, worker_error=False):
        draining = threading.Event()
        finished = threading.Event()
        errors = []

        class ObservedFuture(Future):
            def result(self, timeout=None):
                draining.set()
                return super().result(timeout)

        future = ObservedFuture()
        future.set_running_or_notify_cancel()
        feed = SimpleNamespace(
            name="test", slug="test", tags=Mock(values_list=Mock(return_value=[]))
        )
        feeds = [feed, feed] if submission_error else [feed]
        original_open = open

        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            lock_path = Path(directory) / "frequency.lock"

            def open_lock(path, *args, **kwargs):
                self.assertEqual(path, "/tmp/update_feeds_30_min.lock")
                return original_open(lock_path, *args, **kwargs)

            stack.enter_context(patch.object(cmd, "open", side_effect=open_lock, create=True))
            manager = stack.enter_context(patch.object(cmd, "task_manager"))
            manager.submit_task.side_effect = (
                [future, RuntimeError("submission failed")]
                if submission_error
                else [future]
            )
            stack.enter_context(patch.object(cmd, "wait", return_value=(set(), {future})))
            cache = stack.enter_context(patch.object(cmd, "cache_rss"))
            stack.enter_context(patch.object(cmd, "cache_tag"))
            tags = stack.enter_context(patch.object(cmd, "Tag"))
            tags.objects.filter.return_value = []
            stack.enter_context(
                patch.object(
                    cmd,
                    "update_feeds_for_frequency",
                    side_effect=lambda **kwargs: cmd.update_multiple_feeds(feeds),
                )
            )
            command = cmd.Command(stdout=io.StringIO(), stderr=io.StringIO())

            def run():
                try:
                    command.handle(frequency="30 min")
                except BaseException as error:
                    errors.append(error)
                finally:
                    finished.set()

            thread = threading.Thread(target=run)
            thread.start()
            try:
                self.assertTrue(draining.wait(2), "Updater returned without draining its worker")
                self.assertFalse(finished.is_set())
                cache.assert_not_called()
                with original_open(lock_path, "a") as lock:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                competing = cmd.Command(stdout=io.StringIO(), stderr=io.StringIO())
                with self.assertRaises(SystemExit) as exit_context:
                    competing.handle(frequency="30 min")
                self.assertEqual(exit_context.exception.code, 0)
                self.assertEqual(manager.submit_task.call_count, len(feeds))
            finally:
                if worker_error:
                    future.set_exception(RuntimeError("worker failed"))
                else:
                    future.set_result(True)
                thread.join(timeout=5)

            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            with original_open(lock_path, "a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if not submission_error:
                self.assertEqual(cache.call_count, 4)

    def test_lock_and_cache_wait_for_unfinished_worker(self):
        self.check_lock_lifetime()

    def test_partial_submission_failure_still_drains_started_worker(self):
        self.check_lock_lifetime(submission_error=True)

    def test_failed_worker_releases_lock_after_completion(self):
        self.check_lock_lifetime(worker_error=True)

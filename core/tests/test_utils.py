from unittest import mock
from django.test import SimpleTestCase

from utils.text_handler import (
    set_translation_display,
    chunk_on_delimiter,
    adaptive_chunking,
)
from utils.task_manager import TaskManager


class TextHandlerTests(SimpleTestCase):
    """Tests for helper functions in utils.text_handler."""

    def test_set_translation_display_variants(self):
        cases = [
            (0, "TRANSLATION"),
            (1, "TRANSLATION || ORIGINAL"),
            (2, "ORIGINAL || TRANSLATION"),
        ]
        for display, expected in cases:
            with self.subTest(display=display):
                self.assertEqual(
                    set_translation_display("ORIGINAL", "TRANSLATION", display),
                    expected,
                )

    @staticmethod
    def _mock_token_count(text: str) -> int:
        """Deterministic token count: 1 char = 1 token."""
        return len(text)

    @mock.patch("utils.text_handler.get_token_count", _mock_token_count.__func__)
    def test_chunk_on_delimiter(self):
        text = "Sentence one. Sentence two. Sentence three."
        chunks = chunk_on_delimiter(text, max_tokens=15, delimiter=".")
        # No chunk should exceed 15 characters/tokens
        self.assertTrue(all(len(c) <= 15 for c in chunks))
        rebuilt = " ".join(c.strip() for c in chunks)
        self.assertIn("Sentence one", rebuilt)
        self.assertIn("Sentence three", rebuilt)

    @mock.patch("utils.text_handler.get_token_count", _mock_token_count.__func__)
    def test_adaptive_chunking_target_chunks(self):
        long_text = "Lorem ipsum dolor sit amet, " * 20
        chunks = adaptive_chunking(long_text, target_chunks=4)
        # adaptive_chunking aims for ~target_chunks within heuristics
        # Algorithm should produce a manageable number of chunks (at least 1).
        self.assertGreaterEqual(len(chunks), 1)
        self.assertLessEqual(len(chunks), 8)
        self.assertIn("Lorem ipsum", " ".join(chunks))


class TaskManagerTests(SimpleTestCase):
    """Tests for utils.task_manager.TaskManager."""

    def test_submit_and_status(self):
        def add(a, b):
            return a + b

        tm = TaskManager(max_workers=2, restart_threshold=10)
        future = tm.submit_task("add", add, 1, 2)
        self.assertEqual(future.result(timeout=5), 3)

        tasks = tm.list_tasks()
        self.assertEqual(len(tasks), 1)
        task_id, info = next(iter(tasks.items()))
        self.assertEqual(info["status"], "completed")
        self.assertEqual(info["result"], 3)
        self.assertEqual(tm.get_task_status(task_id)["status"], "completed")

    def test_restart_threshold(self):
        tm = TaskManager(max_workers=1, restart_threshold=3)
        # Run 4 quick tasks to exceed threshold and trigger restart
        for _ in range(4):
            tm.submit_task("noop", lambda: None).result(timeout=2)
        self.assertLessEqual(tm.tasks_executed_since_restart, 1)

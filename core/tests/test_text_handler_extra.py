from django.test import SimpleTestCase
from unittest import mock

from utils.text_handler import split_large_sentence, chunk_on_delimiter, get_token_count
from utils.task_manager import TaskManager


class TextHandlerExtraTests(SimpleTestCase):
    """Additional coverage for utils.text_handler helpers."""

    @staticmethod
    def _token_1_per_char(text: str) -> int:  # noqa: D401
        return len(text)

    @mock.patch("utils.text_handler.get_token_count", _token_1_per_char.__func__)
    def test_split_large_sentence_respects_max_tokens(self):
        long_sentence = "A" * 120  # 120 tokens under mocked counter
        chunks = split_large_sentence(long_sentence, max_tokens=30, delimiters=[", "])
        # Each chunk should respect max_tokens
        self.assertTrue(all(get_token_count(c) <= 30 for c in chunks))

    @mock.patch("utils.text_handler.get_token_count", _token_1_per_char.__func__)
    def test_chunk_on_delimiter_fallback(self):
        text = "Hello world! How are you? I'm fine"  # No period delimiter
        chunks = chunk_on_delimiter(text, max_tokens=12, delimiter=".")
        # Ensure fallback delimiters used so chunks not exceed 12
        self.assertTrue(all(len(c) <= 12 for c in chunks))
        # Reconstruct should include original words
        self.assertIn("Hello world", " ".join(chunks))


class TaskManagerExtraTests(SimpleTestCase):
    def test_update_progress_and_filter(self):
        tm = TaskManager(max_workers=1)
        fut = tm.submit_task("noop", lambda: None)
        fut.result(timeout=2)
        task_id = next(iter(tm.list_tasks().keys()))
        tm.update_progress(task_id, 50)
        self.assertEqual(tm.get_task_status(task_id)["progress"], 50)
        # Filter listing works
        completed = tm.list_tasks(filter_status="completed")
        self.assertIn(task_id, completed)

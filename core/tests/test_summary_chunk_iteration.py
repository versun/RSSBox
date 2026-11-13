from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from core.tasks.summarize_feeds import _summarize_entry


class SummaryChunkIterationTests(SimpleTestCase):
    def test_all_chunks_are_summarized_once_in_order(self):
        for count in (1, 2, 3, 4, 5):
            for recursive in (False, True):
                with self.subTest(count=count, recursive=recursive):
                    chunks = [f"section {number}" for number in range(count)]
                    summaries = [f"summary {number}" for number in range(count)]
                    agent = Mock()
                    agent.summarize.side_effect = [
                        {"text": summary, "tokens": number + 1}
                        for number, summary in enumerate(summaries)
                    ]
                    with patch(
                        "core.tasks.summarize_feeds.text_handler.adaptive_chunking",
                        return_value=chunks.copy(),
                    ):
                        summary, tokens = _summarize_entry(
                            entry=SimpleNamespace(original_content="An article."),
                            summarizer=agent,
                            target_language="Chinese",
                            min_chunk_size=1,
                            max_chunk_size=100,
                            summarize_recursively=recursive,
                            max_context_chunks=4,
                            max_context_tokens=1000,
                            chunk_delimiter=".",
                            max_chunks_per_entry=20,
                            summary_detail=1,
                        )
                    prompts = [call.kwargs["text"] for call in agent.summarize.call_args_list]
                    processed = [prompt.split("Current text to summarize:\n\n")[-1] for prompt in prompts]
                    self.assertEqual(processed, chunks)
                    self.assertEqual(summary, "\n\n".join(summaries))
                    self.assertEqual(tokens, sum(range(1, count + 1)))
                    if recursive and count > 1:
                        self.assertIn(summaries[0], prompts[1])

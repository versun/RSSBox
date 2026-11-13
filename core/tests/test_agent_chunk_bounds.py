from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import TestCase

from core.models.agent import (
    MAX_CHUNKS_PER_COMPLETION,
    OpenAIAgent,
)
from utils.text_handler import get_token_count


class CompletionChunkBoundsTests(TestCase):
    def setUp(self):
        self.agent = OpenAIAgent.objects.create(
            name="Chunking agent",
            api_key="unused",
            model="test-model",
            max_tokens=600,
            rate_limit_rpm=0,
        )
        self.client = MagicMock()
        self.client.with_options.return_value = self.client
        self.create = self.client.chat.completions.create
        init = patch.object(OpenAIAgent, "_init", return_value=self.client)
        self.init = init.start()
        self.addCleanup(init.stop)

    @staticmethod
    def response(text="result", tokens=5):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop", message=SimpleNamespace(content=text)
                )
            ],
            usage=SimpleNamespace(total_tokens=tokens),
        )

    def test_non_positive_budget_raises_a_clear_error(self):
        self.agent.max_tokens = 150
        with patch("core.models.agent.get_token_count", return_value=100):
            with self.assertRaisesRegex(ValueError, "No usable input tokens"):
                self.agent.completions("text", system_prompt="prompt")

        self.create.assert_not_called()

    def test_small_budget_clamps_min_chunk_size_and_does_not_recurse(self):
        def token_count(text):
            if text == "long text":
                return 2000
            if text == "system":
                return 50
            return 10

        with (
            patch("core.models.agent.get_token_count", side_effect=token_count),
            patch(
                "core.models.agent.adaptive_chunking",
                return_value=["first chunk", "second chunk"],
            ) as chunker,
        ):
            self.create.side_effect = [self.response("one"), self.response("two")]
            result = self.agent.completions("long text", system_prompt="system")

        # max_tokens(600) - system(50) - buffer(100) = 450, below 500.
        chunker.assert_called_once()
        self.assertEqual(chunker.call_args.kwargs["min_chunk_size"], 450)
        self.assertEqual(chunker.call_args.kwargs["max_chunk_size"], 450)
        self.assertEqual(result["text"], "one two")
        self.assertEqual(self.create.call_count, 2)

    def test_user_prompt_is_appended_only_once_across_chunks(self):
        def token_count(text):
            if text == "long text":
                return 2000
            if "EXTRA INSTRUCTIONS" in text:
                return 60
            if text == "system":
                return 50
            return 10

        with (
            patch("core.models.agent.get_token_count", side_effect=token_count),
            patch(
                "core.models.agent.adaptive_chunking",
                return_value=["first chunk", "second chunk"],
            ),
        ):
            self.create.side_effect = [self.response("one"), self.response("two")]
            self.agent.completions(
                "long text",
                system_prompt="system",
                user_prompt="EXTRA INSTRUCTIONS",
            )

        self.assertEqual(self.create.call_count, 2)
        for call in self.create.call_args_list:
            system_message = call.kwargs["messages"][0]["content"]
            self.assertEqual(system_message.count("EXTRA INSTRUCTIONS"), 1)

    def test_long_text_is_chunked_within_the_token_budget(self):
        agent = OpenAIAgent.objects.create(
            name="Real chunking agent",
            api_key="unused",
            model="test-model",
            max_tokens=1200,
            rate_limit_rpm=0,
        )
        prompt = "Summarize this."
        usable = agent.max_tokens - get_token_count(prompt) - 100
        text = "token " * 3000
        self.create.return_value = self.response("piece")

        result = agent.completions(text, system_prompt=prompt)

        call_count = self.create.call_count
        self.assertGreater(call_count, 1)
        self.assertEqual(result["text"], " ".join(["piece"] * call_count))
        self.assertEqual(result["tokens"], 5 * call_count)
        for call in self.create.call_args_list:
            sent_text = call.kwargs["messages"][-1]["content"]
            self.assertLessEqual(get_token_count(sent_text), usable)

    def test_chunk_count_limit_stops_runaway_calls(self):
        def token_count(text):
            if text == "long text":
                return 2000
            if text == "system":
                return 50
            return 10

        chunks = [f"chunk {index}" for index in range(MAX_CHUNKS_PER_COMPLETION + 1)]
        with (
            patch("core.models.agent.get_token_count", side_effect=token_count),
            patch(
                "core.models.agent.adaptive_chunking",
                return_value=chunks,
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "above the limit"):
                self.agent.completions("long text", system_prompt="system")

        self.create.assert_not_called()

    def test_chunk_recursion_depth_is_capped(self):
        def token_count(text):
            return len(text)

        def fake_chunks(text, **kwargs):
            size = len(text)
            if size == 1000:
                return ["a" * 600, "b" * 600]
            if size == 600:
                return ["a" * 550, "b" * 550]
            if size == 550:
                return ["a" * 520, "b" * 520]
            raise AssertionError(f"unexpected chunking call for {size} tokens")

        self.agent.max_tokens = 605
        with (
            patch("core.models.agent.get_token_count", side_effect=token_count),
            patch("core.models.agent.adaptive_chunking", side_effect=fake_chunks),
        ):
            with self.assertRaisesRegex(RuntimeError, "recursion depth"):
                self.agent.completions("x" * 1000, system_prompt="sys")

        self.create.assert_not_called()
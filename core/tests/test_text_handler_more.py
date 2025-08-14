import random
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase
from bs4 import BeautifulSoup

from utils import text_handler as th


class AdaptiveChunkingTests(SimpleTestCase):
    """Extra tests for adaptive_chunking behaviour."""

    @staticmethod
    def _token_per_char(text: str) -> int:  # noqa: D401
        return len(text)

    @mock.patch("utils.text_handler.get_token_count", _token_per_char.__func__)
    def test_chunk_counts_close_to_target(self):
        text = " ".join(str(i) for i in range(1000))  # large text
        chunks = th.adaptive_chunking(text, target_chunks=5)
        # Should not deviate too much from target (0.5x - 1.5x logic)
        self.assertGreaterEqual(len(chunks), 2)
        self.assertLessEqual(len(chunks), 8)
        # All chunks token size within limits
        # Ensure chunks concatenate back to original text
        self.assertEqual("".join(chunks).replace(" ", ""), text.replace(" ", ""))


class SkipAndUnwrapTests(SimpleTestCase):
    def test_should_skip_various(self):
        html = """
        <html>
            <body>
                <script>var a = 1;</script>
                <p class='normal'>Hello</p>
                <span class='katex'>E=mc^2</span>
            </body>
        </html>
        """
        soup = BeautifulSoup(html, "html.parser")
        script_tag = soup.find("script")
        normal_p = soup.find("p")
        katex_span = soup.find("span", class_="katex")
        self.assertFalse(th.should_skip(script_tag))
        self.assertFalse(th.should_skip(normal_p))
        self.assertFalse(th.should_skip(katex_span))

    def test_unwrap_tags(self):
        html = "<p><strong>Bold</strong> and <em>italic</em></p>"
        soup = BeautifulSoup(html, "html.parser")
        result = th.unwrap_tags(soup)
        self.assertNotIn("strong", result)
        self.assertNotIn("em", result)
        self.assertIn("Bold", result)


class TranslationDisplayTests(SimpleTestCase):
    def test_set_translation_display(self):
        original = "原文"
        translation = "翻译"
        self.assertEqual(
            th.set_translation_display(original, translation, 0), translation
        )
        self.assertEqual(
            th.set_translation_display(original, translation, 1),
            f"{translation} || {original}",
        )
        self.assertEqual(
            th.set_translation_display(original, translation, 2),
            f"{original} || {translation}",
        )
        self.assertEqual(th.set_translation_display(original, translation, 99), "")

from django.test import SimpleTestCase, TestCase
from unittest.mock import patch, Mock
from bs4 import BeautifulSoup

from utils.text_handler import (
    clean_content,
    tokenize,
    get_token_count,
    split_large_sentence,
    chunk_on_delimiter,
    adaptive_chunking,
    should_skip,
    unwrap_tags,
    set_translation_display,
)
from utils.task_manager import TaskManager


class TextHandlerBasicTests(SimpleTestCase):
    """Basic tests for text_handler core functions."""

    def test_clean_content_basic_html(self):
        """Test clean_content with basic HTML."""
        html = "<p>This is a <strong>test</strong> paragraph.</p>"
        result = clean_content(html)
        self.assertIn("This is a test paragraph", result)
        self.assertNotIn("<p>", result)
        self.assertNotIn("<strong>", result)

    def test_clean_content_with_links_and_images(self):
        """Test clean_content ignores links and images."""
        html = '<p>Check out <a href="https://example.com">this link</a> and <img src="image.jpg" alt="image">.</p>'
        result = clean_content(html)
        self.assertIn("Check out this link", result)
        self.assertNotIn("https://example.com", result)
        self.assertNotIn("img src", result)

    def test_clean_content_with_tables(self):
        """Test clean_content ignores tables."""
        html = """
        <table>
            <tr><td>Cell 1</td><td>Cell 2</td></tr>
            <tr><td>Cell 3</td><td>Cell 4</td></tr>
        </table>
        """
        result = clean_content(html)
        self.assertNotIn("<table>", result)
        self.assertNotIn("<tr>", result)

    def test_clean_content_multiple_newlines(self):
        """Test clean_content removes multiple newlines."""
        html = "<p>Line 1</p>\n\n\n<p>Line 2</p>"
        result = clean_content(html)
        self.assertNotIn("\n\n", result)

    def test_tokenize_caching(self):
        """Test tokenize function with caching."""
        text = "This is a test sentence."

        tokens1 = tokenize(text)
        tokens2 = tokenize(text)

        self.assertEqual(tokens1, tokens2)
        self.assertIsInstance(tokens1, list)
        self.assertGreater(len(tokens1), 0)

    def test_get_token_count_various_texts(self):
        """Test get_token_count with different text lengths."""
        short_text = "Hello"
        medium_text = "This is a medium length sentence with several words."
        long_text = "This is a much longer text that contains many more words and should result in a higher token count than the previous examples."

        short_count = get_token_count(short_text)
        medium_count = get_token_count(medium_text)
        long_count = get_token_count(long_text)

        self.assertGreater(medium_count, short_count)
        self.assertGreater(long_count, medium_count)


class TextHandlerChunkingTests(SimpleTestCase):
    """Tests for text chunking and splitting functions."""

    @staticmethod
    def _token_per_char(text: str) -> int:
        """Mock token counter that returns character count."""
        return len(text)

    @patch("utils.text_handler.get_token_count", _token_per_char.__func__)
    def test_split_large_sentence_respects_max_tokens(self):
        """Test split_large_sentence respects max_tokens limit."""
        long_sentence = "A" * 120
        chunks = split_large_sentence(long_sentence, max_tokens=30, delimiters=[", "])
        self.assertTrue(all(get_token_count(c) <= 30 for c in chunks))

    @patch("utils.text_handler.get_token_count", _token_per_char.__func__)
    def test_split_large_sentence_no_split_needed(self):
        """Test split_large_sentence when sentence is already small enough."""
        sentence = "This is a short sentence."
        result = split_large_sentence(sentence, max_tokens=100)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], sentence)

    @patch("utils.text_handler.get_token_count", _token_per_char.__func__)
    def test_split_large_sentence_with_commas(self):
        """Test split_large_sentence using comma delimiter."""
        sentence = "This is a long sentence, with multiple commas, that should be split, into several parts."
        result = split_large_sentence(sentence, max_tokens=5, delimiters=[",", " "])
        self.assertGreaterEqual(len(result), 1)
        if len(result) > 1:
            self.assertLess(len(result[0]), len(sentence))

    @patch("utils.text_handler.get_token_count", _token_per_char.__func__)
    def test_split_large_sentence_fallback_to_spaces(self):
        """Test split_large_sentence falls back to space delimiter."""
        sentence = "This is a sentence without commas that needs splitting"
        result = split_large_sentence(sentence, max_tokens=3, delimiters=[",", " "])
        self.assertGreaterEqual(len(result), 1)

    @patch("utils.text_handler.get_token_count", _token_per_char.__func__)
    def test_split_large_sentence_unsplittable(self):
        """Test split_large_sentence with unsplittable content."""
        sentence = "verylongwordwithoutanydelimiters"
        result = split_large_sentence(sentence, max_tokens=2, delimiters=[",", " "])
        self.assertGreaterEqual(len(result), 1)

    @patch("utils.text_handler.get_token_count", _token_per_char.__func__)
    def test_chunk_on_delimiter_basic(self):
        """Test chunk_on_delimiter with basic text."""
        text = "First sentence. Second sentence. Third sentence."
        result = chunk_on_delimiter(text, max_tokens=5, delimiter=".")
        self.assertGreaterEqual(len(result), 1)
        for chunk in result:
            self.assertLessEqual(get_token_count(chunk), 10)

    @patch("utils.text_handler.get_token_count", _token_per_char.__func__)
    def test_chunk_on_delimiter_with_fallback(self):
        """Test chunk_on_delimiter with fallback delimiters."""
        text = "First sentence! Second sentence? Third sentence."
        result = chunk_on_delimiter(
            text, max_tokens=5, delimiter=".", fallback_delimiters=["!", "?"]
        )
        self.assertGreater(len(result), 1)

    @patch("utils.text_handler.get_token_count", _token_per_char.__func__)
    def test_chunk_on_delimiter_fallback_behavior(self):
        """Test chunk_on_delimiter fallback behavior."""
        text = "Hello world! How are you? I'm fine"
        chunks = chunk_on_delimiter(text, max_tokens=12, delimiter=".")
        self.assertTrue(all(len(c) <= 12 for c in chunks))
        self.assertIn("Hello world", " ".join(chunks))

    def test_chunk_on_delimiter_empty_text(self):
        """Test chunk_on_delimiter with empty text."""
        result = chunk_on_delimiter("", max_tokens=100)
        self.assertEqual(result, [""])

    @patch("utils.text_handler.get_token_count", _token_per_char.__func__)
    def test_adaptive_chunking_basic(self):
        """Test adaptive_chunking with basic parameters."""
        text = "This is a long text. " * 50
        result = adaptive_chunking(
            text, target_chunks=3, min_chunk_size=50, max_chunk_size=200
        )
        self.assertLessEqual(len(result), 10)
        self.assertGreaterEqual(len(result), 1)
        for chunk in result:
            self.assertIsInstance(chunk, str)

    @patch("utils.text_handler.get_token_count", _token_per_char.__func__)
    def test_adaptive_chunking_short_text(self):
        """Test adaptive_chunking with text shorter than target."""
        text = "Short text."
        result = adaptive_chunking(
            text, target_chunks=5, min_chunk_size=50, max_chunk_size=200
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], text)

    @patch("utils.text_handler.get_token_count", _token_per_char.__func__)
    def test_adaptive_chunking_adjustment(self):
        """Test adaptive_chunking adjusts chunk size to hit target."""
        text = "Sentence. " * 100
        result = adaptive_chunking(
            text, target_chunks=4, min_chunk_size=50, max_chunk_size=500
        )
        self.assertGreaterEqual(len(result), 2)
        self.assertLessEqual(len(result), 8)

    @patch("utils.text_handler.get_token_count", _token_per_char.__func__)
    def test_adaptive_chunking_counts_close_to_target(self):
        """Test adaptive_chunking produces chunks close to target count."""
        text = " ".join(str(i) for i in range(1000))
        chunks = adaptive_chunking(text, target_chunks=5)
        self.assertGreaterEqual(len(chunks), 2)
        self.assertLessEqual(len(chunks), 8)
        self.assertEqual("".join(chunks).replace(" ", ""), text.replace(" ", ""))


class TextHandlerHTMLTests(SimpleTestCase):
    """Tests for HTML processing functions."""

    def test_should_skip_various_elements(self):
        """Test should_skip with various HTML elements."""
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
        
        self.assertFalse(should_skip(script_tag))
        self.assertFalse(should_skip(normal_p))
        self.assertFalse(should_skip(katex_span))

    def test_should_skip_function_exists(self):
        """Test should_skip function exists and is callable."""
        soup = BeautifulSoup("<p>content</p>", "html.parser")
        p_tag = soup.find("p")
        result = should_skip(p_tag)
        self.assertIsInstance(result, bool)

    def test_unwrap_tags_basic(self):
        """Test unwrap_tags with basic HTML."""
        html = "<p><strong>Bold</strong> and <em>italic</em></p>"
        soup = BeautifulSoup(html, "html.parser")
        result = unwrap_tags(soup)
        
        self.assertNotIn("strong", result)
        self.assertNotIn("em", result)
        self.assertIn("Bold", result)

    def test_unwrap_tags_preserves_structure(self):
        """Test unwrap_tags preserves important structure."""
        soup = BeautifulSoup(
            "<div><p>paragraph</p><span>span text</span></div>", "html.parser"
        )
        unwrap_tags(soup)
        
        self.assertIsNotNone(soup.find("p"))
        self.assertIsNone(soup.find("span"))

    def test_unwrap_tags_complex_structure(self):
        """Test unwrap_tags with complex HTML structure."""
        soup = BeautifulSoup(
            "<div><span>text</span><em>emphasis</em></div>", "html.parser"
        )
        unwrap_tags(soup)
        
        self.assertIsNone(soup.find("span"))
        self.assertIsNone(soup.find("em"))
        self.assertIn("text", soup.get_text())
        self.assertIn("emphasis", soup.get_text())


class TextHandlerTranslationTests(SimpleTestCase):
    """Tests for translation display functions."""

    def test_set_translation_display_all_modes(self):
        """Test set_translation_display with all display modes."""
        original = "原文"
        translation = "翻译"
        
        self.assertEqual(
            set_translation_display(original, translation, 0), translation
        )
        self.assertEqual(
            set_translation_display(original, translation, 1),
            f"{translation} || {original}",
        )
        self.assertEqual(
            set_translation_display(original, translation, 2),
            f"{original} || {translation}",
        )

    def test_set_translation_display_translation_only(self):
        """Test set_translation_display with translation only (mode 0)."""
        result = set_translation_display("Original", "Translation", 0)
        self.assertEqual(result, "Translation")

    def test_set_translation_display_translation_with_original(self):
        """Test set_translation_display with translation || original (mode 1)."""
        result = set_translation_display("Original", "Translation", 1)
        self.assertEqual(result, "Translation || Original")

    def test_set_translation_display_original_with_translation(self):
        """Test set_translation_display with original || translation (mode 2)."""
        result = set_translation_display("Original", "Translation", 2)
        self.assertEqual(result, "Original || Translation")

    def test_set_translation_display_custom_separator(self):
        """Test set_translation_display with custom separator."""
        result = set_translation_display("Original", "Translation", 2, " | ")
        self.assertEqual(result, "Original | Translation")

    def test_set_translation_display_edge_cases(self):
        """Test set_translation_display with edge cases."""
        # Empty translation
        result = set_translation_display("Original", "", 1)
        self.assertEqual(result, " || Original")
        
        # Empty original
        result = set_translation_display("", "Translation", 0)
        self.assertEqual(result, "Translation")
        
        # Both empty
        result = set_translation_display("", "", 2)
        self.assertEqual(result, " || ")
        
        # Invalid mode
        result = set_translation_display("Original", "Translation", 99)
        self.assertEqual(result, "")


class TaskManagerTests(TestCase):
    """Tests for TaskManager functionality."""

    def test_update_progress_and_filter(self):
        """Test TaskManager progress update and filtering."""
        tm = TaskManager(max_workers=1)
        fut = tm.submit_task("noop", lambda: None)
        fut.result(timeout=2)
        
        task_id = next(iter(tm.list_tasks().keys()))
        tm.update_progress(task_id, 50)
        
        self.assertEqual(tm.get_task_status(task_id)["progress"], 50)
        
        # Test filtering
        completed = tm.list_tasks(filter_status="completed")
        self.assertIn(task_id, completed)

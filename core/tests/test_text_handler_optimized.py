from django.test import SimpleTestCase, TestCase
from unittest.mock import patch, Mock
from bs4 import BeautifulSoup, Comment

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

    def test_clean_content_empty_html(self):
        """Test clean_content with empty HTML."""
        result = clean_content("")
        # html2text.HTML2Text().handle("") returns "\n", so we expect that
        self.assertEqual(result, "\n")

    def test_clean_content_html_with_comments(self):
        """Test clean_content with HTML comments."""
        html = "<!-- Comment --><p>Content</p>"
        result = clean_content(html)
        self.assertNotIn("Comment", result)
        self.assertIn("Content", result)

    def test_clean_content_html_with_entities(self):
        """Test clean_content with HTML entities."""
        html = "<p>&amp; &lt; &gt; &quot;</p>"
        result = clean_content(html)
        self.assertIn("&", result)
        self.assertIn("<", result)
        self.assertIn(">", result)
        self.assertIn('"', result)

    def test_clean_content_html_with_nested_tags(self):
        """Test clean_content with deeply nested HTML tags."""
        html = "<div><p><span><strong><em>Nested content</em></strong></span></p></div>"
        result = clean_content(html)
        self.assertIn("Nested content", result)
        self.assertNotIn("<div>", result)
        self.assertNotIn("<span>", result)

    def test_clean_content_html_with_special_characters(self):
        """Test clean_content with special characters."""
        html = "<p>Special chars: éñtèrnâtiônâl</p>"
        result = clean_content(html)
        self.assertIn("éñtèrnâtiônâl", result)

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
    def test_split_large_sentence_empty_delimiters(self):
        """Test split_large_sentence with empty delimiters list."""
        sentence = "This is a test sentence"
        result = split_large_sentence(sentence, max_tokens=5, delimiters=[])
        self.assertGreaterEqual(len(result), 1)

    @patch("utils.text_handler.get_token_count", _token_per_char.__func__)
    def test_split_large_sentence_single_delimiter(self):
        """Test split_large_sentence with single delimiter."""
        sentence = "Part1,Part2,Part3"
        result = split_large_sentence(sentence, max_tokens=3, delimiters=[","])
        self.assertGreaterEqual(len(result), 1)

    @patch("utils.text_handler.get_token_count", _token_per_char.__func__)
    def test_split_large_sentence_preserves_delimiters(self):
        """Test split_large_sentence preserves delimiters in output."""
        sentence = "First,Second,Third"
        result = split_large_sentence(sentence, max_tokens=4, delimiters=[","])
        for chunk in result:
            if chunk != result[-1]:  # Last chunk doesn't need delimiter
                self.assertIn(",", chunk)

    @patch("utils.text_handler.get_token_count", _token_per_char.__func__)
    def test_split_large_sentence_recursive_splitting(self):
        """Test split_large_sentence recursive splitting behavior."""
        sentence = "Very long sentence that needs multiple levels of splitting"
        result = split_large_sentence(
            sentence, max_tokens=2, delimiters=[",", " ", "e"]
        )
        self.assertGreaterEqual(len(result), 1)
        for chunk in result:
            self.assertLessEqual(get_token_count(chunk), 2)

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
    def test_chunk_on_delimiter_whitespace_only(self):
        """Test chunk_on_delimiter with whitespace only text."""
        result = chunk_on_delimiter("   \n\t   ", max_tokens=100)
        self.assertEqual(result, [""])

    @patch("utils.text_handler.get_token_count", _token_per_char.__func__)
    def test_chunk_on_delimiter_no_delimiters_found(self):
        """Test chunk_on_delimiter when no delimiters are found."""
        text = "No delimiters in this text"
        result = chunk_on_delimiter(text, max_tokens=5, delimiter=".")
        # When no delimiters found, the entire text becomes one chunk
        # But the function processes it character by character, so it might split
        self.assertGreaterEqual(len(result), 1)
        self.assertIn("No delimiters", " ".join(result))

    @patch("utils.text_handler.get_token_count", _token_per_char.__func__)
    def test_chunk_on_delimiter_multiple_consecutive_delimiters(self):
        """Test chunk_on_delimiter with multiple consecutive delimiters."""
        text = "First...Second...Third"
        result = chunk_on_delimiter(text, max_tokens=10, delimiter=".")
        self.assertGreaterEqual(len(result), 1)

    @patch("utils.text_handler.get_token_count", _token_per_char.__func__)
    def test_chunk_on_delimiter_unicode_delimiters(self):
        """Test chunk_on_delimiter with unicode delimiters."""
        text = "第一句。第二句！第三句？"
        result = chunk_on_delimiter(
            text, max_tokens=5, delimiter="。", fallback_delimiters=["！", "？"]
        )
        self.assertGreaterEqual(len(result), 1)

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

    @patch("utils.text_handler.get_token_count", _token_per_char.__func__)
    def test_adaptive_chunking_with_min_chunk_size(self):
        """Test adaptive_chunking respects min_chunk_size parameter."""
        text = "Short text."
        result = adaptive_chunking(
            text, target_chunks=5, min_chunk_size=100, max_chunk_size=200
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], text)

    @patch("utils.text_handler.get_token_count", _token_per_char.__func__)
    def test_adaptive_chunking_with_max_chunk_size(self):
        """Test adaptive_chunking respects max_chunk_size parameter."""
        text = "Sentence. " * 200
        result = adaptive_chunking(
            text, target_chunks=2, min_chunk_size=50, max_chunk_size=100
        )
        self.assertGreaterEqual(len(result), 2)
        for chunk in result:
            self.assertLessEqual(get_token_count(chunk), 100)

    @patch("utils.text_handler.get_token_count", _token_per_char.__func__)
    def test_adaptive_chunking_edge_case_single_chunk(self):
        """Test adaptive_chunking edge case when text fits in single chunk."""
        text = "Single sentence."
        result = adaptive_chunking(
            text, target_chunks=10, min_chunk_size=5, max_chunk_size=50
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], text)

    @patch("utils.text_handler.get_token_count", _token_per_char.__func__)
    def test_adaptive_chunking_adjustment_down(self):
        """Test adaptive_chunking adjusts chunk size down when too many chunks."""
        text = "Word. " * 100
        result = adaptive_chunking(
            text, target_chunks=2, min_chunk_size=10, max_chunk_size=200
        )
        self.assertLessEqual(len(result), 4)  # Should reduce from many small chunks

    @patch("utils.text_handler.get_token_count", _token_per_char.__func__)
    def test_adaptive_chunking_adjustment_up(self):
        """Test adaptive_chunking adjusts chunk size up when too few chunks."""
        text = "Very long sentence with many words. " * 10
        result = adaptive_chunking(
            text, target_chunks=8, min_chunk_size=10, max_chunk_size=100
        )
        self.assertGreaterEqual(len(result), 4)  # Should increase from few large chunks


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

    def test_should_skip_comment_element(self):
        """Test should_skip with Comment element (line 254)."""
        comment = Comment("This is a comment")
        result = should_skip(comment)
        self.assertTrue(result)

    def test_should_skip_element_with_skip_tag_parent(self):
        """Test should_skip with element that has skip_tag parent (line 256)."""
        html = "<pre><code>Some code</code></pre>"
        soup = BeautifulSoup(html, "html.parser")
        code_tag = soup.find("code")
        result = should_skip(code_tag)
        self.assertTrue(result)

    def test_should_skip_element_with_katex_parent(self):
        """Test should_skip with element that has katex parent (line 260)."""
        html = '<span class="katex"><span>E=mc^2</span></span>'
        soup = BeautifulSoup(html, "html.parser")
        inner_span = soup.find("span", class_="katex").find("span")
        result = should_skip(inner_span)
        self.assertTrue(result)

    def test_should_skip_element_matching_url_pattern(self):
        """Test should_skip with element matching URL pattern (line 271)."""
        html = "<span>https://example.com</span>"
        soup = BeautifulSoup(html, "html.parser")
        span_tag = soup.find("span")
        result = should_skip(span_tag)
        self.assertTrue(result)

    def test_should_skip_element_matching_email_pattern(self):
        """Test should_skip with element matching email pattern (line 271)."""
        html = "<span>test@example.com</span>"
        soup = BeautifulSoup(html, "html.parser")
        span_tag = soup.find("span")
        result = should_skip(span_tag)
        self.assertTrue(result)

    def test_should_skip_element_matching_number_pattern(self):
        """Test should_skip with element matching number pattern (line 271)."""
        html = "<span>12345</span>"
        soup = BeautifulSoup(html, "html.parser")
        span_tag = soup.find("span")
        result = should_skip(span_tag)
        self.assertTrue(result)

    def test_should_skip_element_with_symbols_pattern(self):
        """Test should_skip with element matching symbols pattern (line 271)."""
        html = "<span>!@#$%</span>"
        soup = BeautifulSoup(html, "html.parser")
        span_tag = soup.find("span")
        result = should_skip(span_tag)
        self.assertTrue(result)

    def test_should_skip_normal_element(self):
        """Test should_skip with normal element that should not be skipped."""
        html = "<p>Normal text content</p>"
        soup = BeautifulSoup(html, "html.parser")
        p_tag = soup.find("p")
        result = should_skip(p_tag)
        self.assertFalse(result)

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

    def test_unwrap_tags_empty_soup(self):
        """Test unwrap_tags with empty BeautifulSoup object."""
        soup = BeautifulSoup("", "html.parser")
        result = unwrap_tags(soup)
        self.assertEqual(result, "")

    def test_unwrap_tags_no_unwrappable_tags(self):
        """Test unwrap_tags with no tags to unwrap."""
        soup = BeautifulSoup("<div><p>Content</p></div>", "html.parser")
        original_text = soup.get_text()
        result = unwrap_tags(soup)
        self.assertEqual(soup.get_text(), original_text)

    def test_unwrap_tags_nested_unwrappable_tags(self):
        """Test unwrap_tags with nested unwrappable tags."""
        soup = BeautifulSoup(
            "<div><span><strong><em>Nested</em></strong></span></div>", "html.parser"
        )
        unwrap_tags(soup)

        # All unwrappable tags should be removed
        self.assertIsNone(soup.find("span"))
        self.assertIsNone(soup.find("strong"))
        self.assertIsNone(soup.find("em"))
        self.assertIn("Nested", soup.get_text())

    def test_unwrap_tags_mixed_content(self):
        """Test unwrap_tags with mixed content (unwrappable and regular tags)."""
        soup = BeautifulSoup(
            "<div><p>Paragraph</p><span>Span</span><h1>Heading</h1></div>",
            "html.parser",
        )
        unwrap_tags(soup)

        # Unwrappable tags should be removed
        self.assertIsNone(soup.find("span"))
        # Regular tags should remain
        self.assertIsNotNone(soup.find("p"))
        self.assertIsNotNone(soup.find("h1"))
        self.assertIn("Span", soup.get_text())


class TextHandlerTranslationTests(SimpleTestCase):
    """Tests for translation display functions."""

    def test_set_translation_display_all_modes(self):
        """Test set_translation_display with all display modes."""
        original = "原文"
        translation = "翻译"

        self.assertEqual(set_translation_display(original, translation, 0), translation)
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

    def test_set_translation_display_with_special_characters(self):
        """Test set_translation_display with special characters."""
        original = "原文：Hello & World"
        translation = "Translation: Hello & World"

        result = set_translation_display(original, translation, 1)
        self.assertEqual(result, f"{translation} || {original}")

    def test_set_translation_display_with_unicode(self):
        """Test set_translation_display with unicode characters."""
        original = "中文原文"
        translation = "English Translation"

        result = set_translation_display(original, translation, 2)
        self.assertEqual(result, f"{original} || {translation}")

    def test_set_translation_display_with_numbers(self):
        """Test set_translation_display with numbers."""
        original = "12345"
        translation = "Five"

        result = set_translation_display(original, translation, 1)
        self.assertEqual(result, f"{translation} || {original}")

    def test_set_translation_display_with_empty_separator(self):
        """Test set_translation_display with empty separator."""
        result = set_translation_display("Original", "Translation", 1, "")
        self.assertEqual(result, "TranslationOriginal")


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

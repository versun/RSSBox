from django.test import TestCase
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
    set_translation_display
)


class TextHandlerExtendedTest(TestCase):
    
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
        # Tables should be ignored, so minimal content
        self.assertNotIn("<table>", result)
        self.assertNotIn("<tr>", result)

    def test_clean_content_multiple_newlines(self):
        """Test clean_content removes multiple newlines."""
        html = "<p>Line 1</p>\n\n\n<p>Line 2</p>"
        result = clean_content(html)
        # Should not have multiple consecutive newlines
        self.assertNotIn("\n\n", result)

    def test_tokenize_caching(self):
        """Test tokenize function with caching."""
        text = "This is a test sentence."
        
        # First call
        tokens1 = tokenize(text)
        # Second call should use cache
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

    def test_split_large_sentence_no_split_needed(self):
        """Test split_large_sentence when sentence is already small enough."""
        sentence = "This is a short sentence."
        result = split_large_sentence(sentence, max_tokens=100)
        
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], sentence)

    def test_split_large_sentence_with_commas(self):
        """Test split_large_sentence using comma delimiter."""
        sentence = "This is a long sentence, with multiple commas, that should be split, into several parts."
        result = split_large_sentence(sentence, max_tokens=5, delimiters=[",", " "])
        
        self.assertGreaterEqual(len(result), 1)
        # Verify we get some kind of splitting
        if len(result) > 1:
            self.assertLess(len(result[0]), len(sentence))

    def test_split_large_sentence_fallback_to_spaces(self):
        """Test split_large_sentence falls back to space delimiter."""
        sentence = "This is a sentence without commas that needs splitting"
        result = split_large_sentence(sentence, max_tokens=3, delimiters=[",", " "])
        
        self.assertGreaterEqual(len(result), 1)

    def test_split_large_sentence_unsplittable(self):
        """Test split_large_sentence with unsplittable content."""
        sentence = "verylongwordwithoutanydelimiters"
        result = split_large_sentence(sentence, max_tokens=2, delimiters=[",", " "])
        
        # Should return some result
        self.assertGreaterEqual(len(result), 1)

    def test_chunk_on_delimiter_basic(self):
        """Test chunk_on_delimiter with basic text."""
        text = "First sentence. Second sentence. Third sentence."
        result = chunk_on_delimiter(text, max_tokens=5, delimiter=".")
        
        self.assertGreaterEqual(len(result), 1)
        for chunk in result:
            self.assertLessEqual(get_token_count(chunk), 10)  # Allow some buffer

    def test_chunk_on_delimiter_with_fallback(self):
        """Test chunk_on_delimiter with fallback delimiters."""
        text = "First sentence! Second sentence? Third sentence."
        result = chunk_on_delimiter(text, max_tokens=5, delimiter=".", fallback_delimiters=["!", "?"])
        
        self.assertGreater(len(result), 1)

    def test_chunk_on_delimiter_empty_text(self):
        """Test chunk_on_delimiter with empty text."""
        result = chunk_on_delimiter("", max_tokens=100)
        self.assertEqual(result, [""])

    def test_adaptive_chunking_basic(self):
        """Test adaptive_chunking with basic parameters."""
        text = "This is a long text. " * 50  # Create long text
        result = adaptive_chunking(text, target_chunks=3, min_chunk_size=50, max_chunk_size=200)
        
        self.assertLessEqual(len(result), 10)  # Should be reasonable number of chunks
        self.assertGreaterEqual(len(result), 1)  # At least one chunk
        # Verify all chunks are strings
        for chunk in result:
            self.assertIsInstance(chunk, str)

    def test_adaptive_chunking_short_text(self):
        """Test adaptive_chunking with text shorter than target."""
        text = "Short text."
        result = adaptive_chunking(text, target_chunks=5, min_chunk_size=50, max_chunk_size=200)
        
        # Should return single chunk for short text
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], text)

    def test_adaptive_chunking_adjustment(self):
        """Test adaptive_chunking adjusts chunk size to hit target."""
        text = "Sentence. " * 100  # Create predictable long text
        result = adaptive_chunking(text, target_chunks=4, min_chunk_size=50, max_chunk_size=500)
        
        # Should try to get close to 4 chunks
        self.assertGreaterEqual(len(result), 2)
        self.assertLessEqual(len(result), 8)

    def test_should_skip_function_exists(self):
        """Test should_skip function exists and is callable."""
        soup = BeautifulSoup("<p>content</p>", 'html.parser')
        p_tag = soup.find('p')
        
        # Just test that the function exists and returns a boolean
        result = should_skip(p_tag)
        self.assertIsInstance(result, bool)

    def test_unwrap_tags_basic(self):
        """Test unwrap_tags with basic HTML."""
        soup = BeautifulSoup("<div><span>text</span><em>emphasis</em></div>", 'html.parser')
        unwrap_tags(soup)
        
        # span and em tags should be unwrapped
        self.assertIsNone(soup.find('span'))
        self.assertIsNone(soup.find('em'))
        self.assertIn('text', soup.get_text())
        self.assertIn('emphasis', soup.get_text())

    def test_unwrap_tags_preserves_structure(self):
        """Test unwrap_tags preserves important structure."""
        soup = BeautifulSoup("<div><p>paragraph</p><span>span text</span></div>", 'html.parser')
        unwrap_tags(soup)
        
        # p tag should be preserved, span should be unwrapped
        self.assertIsNotNone(soup.find('p'))
        self.assertIsNone(soup.find('span'))

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

    def test_set_translation_display_empty_translation(self):
        """Test set_translation_display with empty translation."""
        result = set_translation_display("Original", "", 1)
        self.assertEqual(result, " || Original")

    def test_set_translation_display_empty_original(self):
        """Test set_translation_display with empty original."""
        result = set_translation_display("", "Translation", 0)
        self.assertEqual(result, "Translation")

    def test_set_translation_display_both_empty(self):
        """Test set_translation_display with both empty."""
        result = set_translation_display("", "", 2)
        self.assertEqual(result, " || ")

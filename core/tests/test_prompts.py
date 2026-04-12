from django.test import SimpleTestCase


class PromptDefaultsTests(SimpleTestCase):
    def test_settings_prompt_defaults_are_reexported_from_prompts_module(self):
        from config import settings
        from core.prompts import (
            DEFAULT_CONTENT_TRANSLATE_PROMPT,
            DEFAULT_FILTER_PROMPT,
            DEFAULT_SUMMARY_PROMPT,
            DEFAULT_TITLE_TRANSLATE_PROMPT,
            OUTPUT_FORMAT_FOR_FILTER_PROMPT,
        )

        self.assertEqual(
            settings.default_title_translate_prompt,
            DEFAULT_TITLE_TRANSLATE_PROMPT,
        )
        self.assertEqual(
            settings.default_content_translate_prompt,
            DEFAULT_CONTENT_TRANSLATE_PROMPT,
        )
        self.assertEqual(settings.default_summary_prompt, DEFAULT_SUMMARY_PROMPT)
        self.assertEqual(settings.default_filter_prompt, DEFAULT_FILTER_PROMPT)
        self.assertEqual(
            settings.output_format_for_filter_prompt,
            OUTPUT_FORMAT_FOR_FILTER_PROMPT,
        )

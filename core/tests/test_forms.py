import pytest
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from core.forms.feed_form import FeedForm
from core.models import Feed
from core.models.agent import OpenAIAgent


class FeedFormTest(TestCase):
    """Tests for `FeedForm` ensuring internal helpers and save logic work."""

    def setUp(self):
        # Minimal valid OpenAIAgent for translator / summarizer selections
        self.agent = OpenAIAgent.objects.create(name="Test Agent", api_key="key", valid=True)
        self.ct = ContentType.objects.get_for_model(OpenAIAgent)

    def test_initial_values_for_existing_instance(self):
        """`_set_initial_values` should populate initial data for the form."""
        feed = Feed.objects.create(
            feed_url="https://example.com/rss.xml",
            update_frequency=15,
            translate_title=True,
            translate_content=False,
            summary=True,
            translator_content_type=self.ct,
            translator_object_id=self.agent.id,
            summarizer_content_type=self.ct,
            summarizer_object_id=self.agent.id,
        )

        form = FeedForm(instance=feed)

        # Check translator / summarizer initial
        expected_agent_value = f"{self.ct.id}:{self.agent.id}"
        assert form.fields["translator_option"].initial == expected_agent_value
        assert form.fields["summary_engine_option"].initial == expected_agent_value

        # Check frequency and translation options initial
        assert form.fields["simple_update_frequency"].initial == 15
        assert set(form.fields["translation_options"].initial) == {"title", "summary"}

    def test_save_processes_custom_fields(self):
        """The overridden `save` should correctly transfer cleaned data to the model."""
        form_data = {
            # model mandatory fields
            "update_frequency": 60,
            "max_posts": 20,
            "translation_display": 0,
            "total_tokens": 0,
            "total_characters": 0,
            "feed_url": "https://another.com/rss.xml",
            "simple_update_frequency": 60,
            "translation_options": ["title", "content"],
            "translator_option": f"{self.ct.id}:{self.agent.id}",
            "summary_engine_option": f"{self.ct.id}:{self.agent.id}",
            # minimal required model fields (others inherit defaults)
            "target_language": "English",  # valid according to settings.TRANSLATION_LANGUAGES
        }

        form = FeedForm(data=form_data)
        assert form.is_valid(), form.errors
        feed: Feed = form.save()

        # Frequency should be stored as int and match form
        assert feed.update_frequency == 60

        # Translation option flags processed correctly
        assert feed.translate_title is True
        assert feed.translate_content is True
        assert feed.summary is False  # not supplied in translation_options

        # Translator / summarizer foreign keys set
        assert feed.translator_content_type_id == self.ct.id
        assert feed.translator_object_id == self.agent.id
        assert feed.summarizer_content_type_id == self.ct.id
        assert feed.summarizer_object_id == self.agent.id

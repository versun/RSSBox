from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from core.forms.feed_form import FeedForm
from core.models import Feed
from core.models.agent import OpenAIAgent


class FeedFormTest(TestCase):
    """Tests for FeedForm functionality."""

    def setUp(self):
        self.agent = OpenAIAgent.objects.create(
            name="Test Agent", api_key="key", valid=True
        )
        self.ct = ContentType.objects.get_for_model(OpenAIAgent)
        self.agent_value = f"{self.ct.id}:{self.agent.id}"

    def test_form_functionality(self):
        """Test form initial values and save processing."""
        # Test initial values for existing instance
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
        assert form.fields["translator_option"].initial == self.agent_value
        assert form.fields["summary_engine_option"].initial == self.agent_value
        assert form.fields["simple_update_frequency"].initial == 15
        assert set(form.fields["translation_options"].initial) == {"title", "summary"}
        
        # Test save processes custom fields
        form_data = {
            "update_frequency": 60,
            "max_posts": 20,
            "translation_display": 0,
            "total_tokens": 0,
            "total_characters": 0,
            "feed_url": "https://another.com/rss.xml",
            "simple_update_frequency": 60,
            "translation_options": ["title", "content"],
            "translator_option": self.agent_value,
            "summary_engine_option": self.agent_value,
            "target_language": "English",
        }
        
        form = FeedForm(data=form_data)
        assert form.is_valid(), form.errors
        saved_feed = form.save()
        
        assert saved_feed.update_frequency == 60
        assert saved_feed.translate_title is True
        assert saved_feed.translate_content is True
        assert saved_feed.summary is False
        assert saved_feed.translator_content_type_id == self.ct.id
        assert saved_feed.translator_object_id == self.agent.id
        assert saved_feed.summarizer_content_type_id == self.ct.id
        assert saved_feed.summarizer_object_id == self.agent.id

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
import uuid

from core.forms.feed_form import FeedForm
from core.models import Feed
from core.models.agent import OpenAIAgent


class FeedFormTest(TestCase):
    """Tests for FeedForm functionality."""

    def setUp(self):
        self.agent = OpenAIAgent.objects.create(
            name=f"Test Agent {uuid.uuid4()}", api_key="key", valid=True
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
            summarizer=self.agent,
        )

        form = FeedForm(instance=feed)
        assert form.fields["translator_option"].initial == self.agent_value
        # summary_engine_option 字段不存在，已删除
        assert form.fields["simple_update_frequency"].initial == 15
        assert form.fields["translate_title"].initial == True
        assert form.fields["translate_content"].initial == False
        assert form.fields["summary"].initial == True

        # Test save processes custom fields
        form_data = {
            "update_frequency": 60,
            "max_posts": 20,
            "translation_display": 0,
            "total_tokens": 0,
            "total_characters": 0,
            "feed_url": "https://another.com/rss.xml",
            "simple_update_frequency": 60,
            "translate_title": True,
            "translate_content": True,
            "summary": False,
            "translator_option": self.agent_value,
            # summary_engine_option 字段不存在，已删除
            "target_language": "English",
            "summarizer": self.agent.id,
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
        assert saved_feed.summarizer_id == self.agent.id

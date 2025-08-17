from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from core.forms.filter_form import FilterForm
from core.models import Filter
from core.models.agent import OpenAIAgent


class FilterFormTest(TestCase):
    """Tests for FilterForm functionality."""

    def setUp(self):
        self.agent = OpenAIAgent.objects.create(
            name="Test Agent", api_key="key", valid=True
        )
        self.ct = ContentType.objects.get_for_model(OpenAIAgent)
        self.agent_value = f"{self.ct.id}:{self.agent.id}"

    def test_form_functionality(self):
        """Test form initial values and save processing."""
        # Test initial values for existing instance
        flt = Filter.objects.create(
            name="F1",
            filter_original_title=True,
            filter_translated_content=True,
            agent_content_type=self.ct,
            agent_object_id=self.agent.id,
        )
        
        form = FilterForm(instance=flt)
        assert form.fields["agent_option"].initial == self.agent_value
        expected_targets = {"original_title", "original_content", "translated_content"}
        assert set(form.fields["target_field"].initial) == expected_targets
        
        # Test save processes custom fields
        form_data = {
            "total_tokens": 0,
            "name": "My Filter",
            "operation": Filter.EXCLUDE,
            "filter_method": Filter.KEYWORD_ONLY,
            "target_field": ["original_content", "translated_title"],
            "agent_option": self.agent_value,
            "keywords": "python, django",
        }
        
        form = FilterForm(data=form_data)
        assert form.is_valid(), form.errors
        saved_filter = form.save()
        form.save_m2m()
        saved_filter.refresh_from_db()
        
        assert saved_filter.filter_original_title is False
        assert saved_filter.filter_original_content is True
        assert saved_filter.filter_translated_title is True
        assert saved_filter.filter_translated_content is False
        assert saved_filter.agent_content_type_id == self.ct.id
        assert saved_filter.agent_object_id == self.agent.id
        
        tags = sorted([tag.name.lower() for tag in saved_filter.keywords.all()])
        assert tags == ["django", "python"]

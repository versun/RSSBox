from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from core.forms.filter_form import FilterForm
from core.models import Filter
from core.models.agent import OpenAIAgent


class FilterFormTest(TestCase):
    """Tests to verify FilterForm helper logic and save behaviour."""

    def setUp(self):
        # Ensure an AI agent exists for option choices
        self.agent = OpenAIAgent.objects.create(
            name="Test Agent", api_key="key", valid=True
        )
        self.ct = ContentType.objects.get_for_model(OpenAIAgent)

    def _agent_value(self):
        return f"{self.ct.id}:{self.agent.id}"

    def test_initial_values_for_existing_instance(self):
        """_set_initial_values of FilterForm should populate initial dict."""
        flt = Filter.objects.create(
            name="F1",
            filter_original_title=True,
            filter_translated_content=True,
            agent_content_type=self.ct,
            agent_object_id=self.agent.id,
        )

        form = FilterForm(instance=flt)

        # Agent initial
        assert form.fields["agent_option"].initial == self._agent_value()
        # target_field initial should reflect true flags
        expected_targets = {"original_title", "original_content", "translated_content"}
        assert set(form.fields["target_field"].initial) == expected_targets

    def test_save_processes_custom_fields(self):
        """save() should transfer cleaned data to model flags + agent."""
        form_data = {
            "total_tokens": 0,
            "name": "My Filter",
            "operation": Filter.EXCLUDE,
            "filter_method": Filter.KEYWORD_ONLY,
            "target_field": ["original_content", "translated_title"],
            "agent_option": self._agent_value(),
            "keywords": "python, django",
        }

        form = FilterForm(data=form_data)
        assert form.is_valid(), form.errors
        flt: Filter = form.save()
        form.save_m2m()
        flt.refresh_from_db()

        # Flags based on target_field selections
        assert flt.filter_original_title is False
        assert flt.filter_original_content is True
        assert flt.filter_translated_title is True
        assert flt.filter_translated_content is False

        # Agent relation set
        assert flt.agent_content_type_id == self.ct.id
        assert flt.agent_object_id == self.agent.id

        # Keywords saved as tags
        tags = sorted([tag.name.lower() for tag in flt.keywords.all()])
        assert tags == ["django", "python"]

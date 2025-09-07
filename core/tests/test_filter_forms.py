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

    def test_form_functionality(self):
        """Test form initial values and save processing."""
        # Test initial values for existing instance
        flt = Filter.objects.create(
            name="F1",
            filter_original_title=True,
            filter_original_content=False,  # Explicitly set to False
            filter_translated_content=True,
            agent=self.agent,
        )

        form = FilterForm(instance=flt)
        # Check that the form instance has the correct agent
        assert form.instance.agent == self.agent
        expected_targets = {"original_title", "translated_content"}
        assert set(form.fields["target_field"].initial) == expected_targets

        # Test save processes custom fields
        form_data = {
            "total_tokens": 0,
            "name": "My Filter",
            "operation": Filter.EXCLUDE,
            "filter_method": Filter.KEYWORD_ONLY,
            "target_field": ["original_content", "translated_title"],
            "agent": self.agent.id,
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
        assert saved_filter.agent == self.agent

        tags = sorted([tag.name.lower() for tag in saved_filter.keywords.all()])
        assert tags == ["django", "python"]

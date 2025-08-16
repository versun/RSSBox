from django.test import TestCase
from core.forms.tag_form import TagForm
from core.models import Tag


class TagFormTestCase(TestCase):
    """Test cases for TagForm"""

    def test_tag_form_meta_model(self):
        """Test TagForm Meta model is Tag"""
        self.assertEqual(TagForm.Meta.model, Tag)

    def test_tag_form_meta_exclude(self):
        """Test TagForm Meta exclude fields"""
        expected_exclude = ["total_tokens", "last_updated", "etag"]
        self.assertEqual(TagForm.Meta.exclude, expected_exclude)

    def test_tag_form_valid_data(self):
        """Test TagForm with valid data"""
        form_data = {
            'name': 'Test Tag',
            'slug': 'test-tag'
        }
        form = TagForm(data=form_data)
        self.assertTrue(form.is_valid())

    def test_tag_form_save(self):
        """Test TagForm save functionality"""
        form_data = {
            'name': 'Test Tag',
            'slug': 'test-tag'
        }
        form = TagForm(data=form_data)
        self.assertTrue(form.is_valid())
        tag = form.save()
        self.assertEqual(tag.name, 'Test Tag')
        self.assertEqual(tag.slug, 'test-tag')

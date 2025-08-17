from django.test import TestCase
from core.forms.tag_form import TagForm
from core.models import Tag


class TagFormTestCase(TestCase):
    """Test cases for TagForm"""

    def test_tag_form_meta_configuration(self):
        """Test TagForm Meta model and exclude fields."""
        self.assertEqual(TagForm.Meta.model, Tag)
        expected_exclude = ["total_tokens", "last_updated", "etag"]
        self.assertEqual(TagForm.Meta.exclude, expected_exclude)

    def test_tag_form_validation_and_save(self):
        """Test TagForm validation and save functionality."""
        form_data = {
            'name': 'Test Tag',
            'slug': 'test-tag'
        }
        form = TagForm(data=form_data)
        self.assertTrue(form.is_valid())
        
        # Test save functionality
        tag = form.save()
        self.assertEqual(tag.name, 'Test Tag')
        self.assertEqual(tag.slug, 'test-tag')
        
        # Verify tag was saved to database
        self.assertTrue(Tag.objects.filter(name='Test Tag', slug='test-tag').exists())

    def test_tag_form_edge_cases(self):
        """Test TagForm with edge cases and auto-slug generation."""
        # Test empty form (should be valid since name is nullable)
        empty_form = TagForm(data={})
        self.assertTrue(empty_form.is_valid())
        empty_tag = empty_form.save()
        self.assertIsNone(empty_tag.name)
        self.assertIsNotNone(empty_tag.slug)  # AutoSlugField should generate something
        
        # Test form with only name (slug should be auto-generated)
        name_only_form = TagForm(data={'name': 'Auto Slug Tag'})
        self.assertTrue(name_only_form.is_valid())
        tag = name_only_form.save()
        self.assertEqual(tag.name, 'Auto Slug Tag')
        self.assertEqual(tag.slug, 'auto-slug-tag')  # Should be auto-generated from name
        
        # Test form with duplicate slug handling
        duplicate_form = TagForm(data={'name': 'Auto Slug Tag'})
        self.assertTrue(duplicate_form.is_valid())
        duplicate_tag = duplicate_form.save()
        self.assertNotEqual(tag.slug, duplicate_tag.slug)  # Should be unique

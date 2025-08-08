from django import forms
from django.utils.translation import gettext_lazy as _
from core.models import Tag


class TagForm(forms.ModelForm):
    class Meta:
        model = Tag
        exclude = ["total_tokens", "last_updated", "etag"]

    # slug = forms.SlugField(required=False, help_text=_("Optional, default use the tag name"))

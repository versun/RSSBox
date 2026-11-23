from django import forms
from django.template.loader import render_to_string

from core.models.agent import OpenAIAgent


class CustomHeadersWidget(forms.Textarea):
    def __init__(self, attrs=None):
        default_attrs = {
            "rows": 4,
            "cols": 60,
            "placeholder": '{"CF-Access-Client-Id": "your-client-id"}',
        }
        if attrs:
            default_attrs.update(attrs)
        super().__init__(attrs=default_attrs)

    def render(self, name, value, attrs=None, renderer=None):
        render_attrs = dict(attrs) if attrs else {}
        existing_class = render_attrs.get("class", self.attrs.get("class", ""))
        classes = existing_class.split()
        if "kv-headers-source" not in classes:
            classes.append("kv-headers-source")
        render_attrs["class"] = " ".join(classes)
        textarea = super().render(name, value, render_attrs, renderer)
        return render_to_string(
            "admin/widgets/custom_headers.html",
            {"textarea": textarea},
        )


class OpenAIAgentForm(forms.ModelForm):
    class Meta:
        model = OpenAIAgent
        fields = "__all__"
        widgets = {
            "custom_headers": CustomHeadersWidget(),
        }

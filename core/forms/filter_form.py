from django import forms
from django.db import transaction
from django.utils.translation import gettext_lazy as _
from core.models import Filter, OpenAIAgent
from tagulous.forms import TagField
from django.forms import CheckboxSelectMultiple


class FilterForm(forms.ModelForm):
    FIELD_CHOICES = (
        ("original_title", _("Original Title")),
        ("original_content", _("Original Content")),
        ("translated_title", _("Translated Title")),
        ("translated_content", _("Translated Content")),
    )
    keywords = TagField(required=False)
    reevaluate_existing = forms.BooleanField(
        required=False,
        initial=False,
        label=_("Reevaluate existing articles"),
        help_text=_(
            "Leave unchecked to keep saved AI decisions. Checking this clears decisions "
            "for every feed and tag using this filter. Their next refresh evaluates recent "
            "articles again and may use substantial AI quota across multiple feeds."
        ),
    )

    class Meta:
        model = Filter
        exclude = [
            "filter_original_title",
            "filter_original_content",
            "filter_translated_title",
            "filter_translated_content",
        ]

    target_field = forms.MultipleChoiceField(
        widget=CheckboxSelectMultiple,
        choices=FIELD_CHOICES,
        required=True,
        label=_("Target Field"),
    )

    def __init__(self, *args, **kwargs):
        super(FilterForm, self).__init__(*args, **kwargs)

        # 限制 agent 字段的选择项，只显示有效的 OpenAI agents
        if "agent" in self.fields:
            self.fields["agent"].queryset = OpenAIAgent.objects.filter(valid=True)
            self.fields["agent"].empty_label = _("Select a valid OpenAI agent...")

        # 如果是已创建的对象，设置默认值
        instance = getattr(self, "instance", None)

        if instance and instance.pk:
            self.fields["target_field"].initial = []
            if instance.filter_original_title:
                self.fields["target_field"].initial.append("original_title")
            if instance.filter_original_content:
                self.fields["target_field"].initial.append("original_content")
            if instance.filter_translated_title:
                self.fields["target_field"].initial.append("translated_title")
            if instance.filter_translated_content:
                self.fields["target_field"].initial.append("translated_content")

    def _process_target_field(self, instance):
        # 清空之前的字段状态
        instance.filter_original_title = False
        instance.filter_original_content = False
        instance.filter_translated_title = False
        instance.filter_translated_content = False

        # 获取选中的字段
        selected_fields = self.cleaned_data.get("target_field", [])

        # 根据选中的字段设置状态
        if "original_title" in selected_fields:
            instance.filter_original_title = True
        if "original_content" in selected_fields:
            instance.filter_original_content = True
        if "translated_title" in selected_fields:
            instance.filter_translated_title = True
        if "translated_content" in selected_fields:
            instance.filter_translated_content = True

    @transaction.atomic
    def _save_m2m(self):
        super()._save_m2m()
        if self.cleaned_data.get("reevaluate_existing") and not getattr(self, "_reevaluated", False):
            self.instance.clear_ai_filter_cache_results()
            self._reevaluated = True

    @transaction.atomic
    def save(self, commit=True):
        instance = super(FilterForm, self).save(commit=False)

        self._process_target_field(instance)

        if commit:
            instance.save()
            self._save_m2m()

        return instance

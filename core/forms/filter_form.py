from django import forms
from django.db import transaction
from django.utils.translation import gettext_lazy as _
from core.models import Filter
from utils.modelAdmin_utils import get_ai_agent_choices
from tagulous.forms import TagField
from django.forms import CheckboxSelectMultiple
 
class FilterForm(forms.ModelForm):
    FIELD_CHOICES = (
        ("original_title", _("Original Title")),
        ("original_content", _("Original Content")),
        ("translated_title", _("Translated Title")),
        ("translated_content", _("Translated Content")),
    )
    agent_option = forms.ChoiceField(
        choices=(),
        required=False,
        help_text=_("Select a valid agent for filtering"),
        label=_("Agent"),
    )
    keywords = TagField(required=False)

    class Meta:
        model = Filter
        exclude=["agent","filter_original_title", "filter_original_content",
                 "filter_translated_title", "filter_translated_content"]

    target_field = forms.MultipleChoiceField(
        widget=CheckboxSelectMultiple,
        choices=FIELD_CHOICES,
        required=True,
        label=_("Target Field"),
    )

    def __init__(self, *args, **kwargs):
        super(FilterForm, self).__init__(*args, **kwargs)
         # 获取过滤器的代理选择项
        self.fields["agent_option"].choices = get_ai_agent_choices()
        
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
            if instance.agent_content_type and instance.agent_object_id:
                self.fields["agent_option"].initial = (
                    f"{instance.agent_content_type.id}:{instance.agent_object_id}"
                )
       
    def _process_agent(self, instance):
        if self.cleaned_data["agent_option"]:
            content_type_id, object_id = map(
                int, self.cleaned_data["agent_option"].split(":")
            )
            instance.agent_content_type_id = content_type_id
            instance.agent_object_id = object_id
        else:
            instance.agent_content_type_id = None
            instance.agent_object_id = None

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
    def save(self, commit=True):
        instance = super(FilterForm, self).save(commit=False)

        self._process_target_field(instance)
        self._process_agent(instance)

        if commit:
            instance.save()

        return instance
    

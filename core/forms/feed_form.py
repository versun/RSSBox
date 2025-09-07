from django import forms
from django.db import transaction
from django.utils.translation import gettext_lazy as _
from core.models import Feed, OpenAIAgent
from utils.modelAdmin_utils import get_all_agent_choices


class FeedForm(forms.ModelForm):
    # 自定义字段，使用ChoiceField生成下拉菜单
    translator_option = forms.ChoiceField(
        choices=(),
        required=False,
        help_text=_("Select a valid agent for translation"),
        label=_("Translator"),
    )
    simple_update_frequency = forms.ChoiceField(
        choices=(
            [
                (5, "5 min"),
                (15, "15 min"),
                (30, "30 min"),
                (60, _("hourly")),
                (1440, _("daily")),
                (10080, _("weekly")),
            ]
        ),
        required=False,
        help_text=_("Select a valid update frequency"),
        label=_("Update Frequency"),
        initial=60,
    )
    # 直接使用三个独立的布尔字段，简单清晰
    translate_title = forms.BooleanField(
        required=False,
        label=_("Translate Title"),
    )
    translate_content = forms.BooleanField(
        required=False,
        label=_("Translate Content"),
    )
    summary = forms.BooleanField(
        required=False,
        label=_("Generate Summary"),
    )

    class Meta:
        model = Feed
        exclude = ["fetch_status", "translation_status", "translator"]

    def __init__(self, *args, **kwargs):
        super(FeedForm, self).__init__(*args, **kwargs)

        # 获取翻译器的选择项，并在开头添加空选项
        agent_choices = get_all_agent_choices()
        self.fields["translator_option"].choices = [("", _("Select a valid agent..."))] + agent_choices
        
        # 限制 summarizer 字段的选择项，只显示有效的 OpenAI agents
        if 'summarizer' in self.fields:
            self.fields['summarizer'].queryset = OpenAIAgent.objects.filter(valid=True)
            self.fields['summarizer'].empty_label = _("Select a valid OpenAI agent...")


        self.fields["name"].widget.attrs.update(
            {
                "placeholder": _("Optional, default use the feed title"),
            }
        )

        self.fields["slug"].widget.attrs.update(
            {
                "placeholder": _("Optional, default use the random slug"),
            }
        )

        # 如果是已创建的对象，设置默认值
        instance = getattr(self, "instance", None)
        if instance and instance.pk:
            self._set_initial_values(instance)

    def _set_initial_values(self, instance):
        if instance.translator_content_type and instance.translator_object_id:
            self.fields[
                "translator_option"
            ].initial = (
                f"{instance.translator_content_type.id}:{instance.translator_object_id}"
            )
        if instance.update_frequency:
            self.fields["simple_update_frequency"].initial = instance.update_frequency

        # 直接设置布尔字段的初始值，无需转换
        self.fields["translate_title"].initial = instance.translate_title
        self.fields["translate_content"].initial = instance.translate_content
        self.fields["summary"].initial = instance.summary

    def _process_translator(self, instance):
        if self.cleaned_data["translator_option"]:
            content_type_id, object_id = map(
                int, self.cleaned_data["translator_option"].split(":")
            )
            instance.translator_content_type_id = content_type_id
            instance.translator_object_id = object_id
        else:
            instance.translator_content_type_id = None
            instance.translator_object_id = None


    def _process_update_frequency(self, instance):
        if self.cleaned_data["simple_update_frequency"]:
            instance.update_frequency = int(
                self.cleaned_data["simple_update_frequency"]
            )
        else:
            instance.update_frequency = 60


    # 重写save方法，以处理自定义字段的数据
    @transaction.atomic
    def save(self, commit=True):
        instance = super(FeedForm, self).save(commit=False)

        self._process_translator(instance)
        self._process_update_frequency(instance)
        # 布尔字段直接由 ModelForm 处理，无需额外处理

        if commit:
            instance.save()

        return instance

from typing import Mapping
from django import forms
from django.core.files.base import File
from django.db import transaction
from django.db.models.base import Model
from django.forms.utils import ErrorList
from django.utils.translation import gettext_lazy as _
from .models import Feed, Filter
from utils.modelAdmin_utils import get_translator_and_summary_choices
from tagulous.forms import TagField
from django.forms import CheckboxSelectMultiple


class FeedForm(forms.ModelForm):
    # 自定义字段，使用ChoiceField生成下拉菜单
    translator_option = forms.ChoiceField(
        choices=(),
        required=False,
        help_text=_("Select a valid translator"),
        label=_("Translator"),
    )
    summary_engine_option = forms.ChoiceField(
        choices=(),
        required=False,
        help_text=_("Select a valid AI engine"),
        label=_("Summarizer"),
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
    translation_options = forms.MultipleChoiceField(
        choices=(
            ("title", _("Title")),
            ("content", _("Content")),
            ("summary", _("Summary")),
        ),
        required=False,
        label=_("Translation Options"),
        widget=forms.CheckboxSelectMultiple,
        initial=[],
    )
    
    class Meta:
        model = Feed
        exclude = ["fetch_status", "translation_status", "translator", "summary_engine"]
        fields = [
            "feed_url",
            "name",
            "slug",
            "max_posts",
            "simple_update_frequency",  # 自定义字段
            "translation_options",
            "target_language",
            "translator_option",  # 自定义字段
            "summary_engine_option",  # 自定义字段
            "translation_display",
            "filters",
            "fetch_article",
            "quality",
            "category",
            "summary_detail",
            "additional_prompt",
        ]

    def __init__(self, *args, **kwargs):
        super(FeedForm, self).__init__(*args, **kwargs)

        # 获取翻译器和摘要引擎的选择项
        (
            self.fields["translator_option"].choices,
            self.fields["summary_engine_option"].choices,
        ) = get_translator_and_summary_choices()

        self.fields["name"].widget.attrs.update(
            {
                "placeholder": _("Optonal, default use the feed title"),
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
        if instance.summarizer_content_type and instance.summarizer_object_id:
            self.fields[
                "summary_engine_option"
            ].initial = (
                f"{instance.summarizer_content_type.id}:{instance.summarizer_object_id}"
            )
        if instance.update_frequency:
            self.fields["simple_update_frequency"].initial = instance.update_frequency

        # 修改后的翻译选项初始化逻辑
        initial_options = []
        if instance.translate_title:
            initial_options.append("title")
        if instance.translate_content:
            initial_options.append("content")
        if instance.summary:
            initial_options.append("summary")
        self.fields["translation_options"].initial = initial_options

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

    def _process_summary_engine(self, instance):
        if self.cleaned_data["summary_engine_option"]:
            summarizer_content_type_id, summarizer_object_id = map(
                int, self.cleaned_data["summary_engine_option"].split(":")
            )
            instance.summarizer_content_type_id = summarizer_content_type_id
            instance.summarizer_object_id = summarizer_object_id
        else:
            instance.summarizer_content_type_id = None
            instance.summarizer_object_id = None

    def _process_update_frequency(self, instance):
        if self.cleaned_data["simple_update_frequency"]:
            instance.update_frequency = int(
                self.cleaned_data["simple_update_frequency"]
            )
        else:
            instance.update_frequency = 60

    def _process_translation_options(self, instance):
        # 确保获取翻译选项数据，默认为空列表
        translation_options = self.cleaned_data.get("translation_options", [])

        # 明确设置每个选项的状态：勾选则为True，否则为False
        instance.translate_title = "title" in translation_options
        instance.translate_content = "content" in translation_options
        instance.summary = "summary" in translation_options

    # 重写save方法，以处理自定义字段的数据
    @transaction.atomic
    def save(self, commit=True):
        instance = super(FeedForm, self).save(commit=False)

        self._process_translator(instance)
        self._process_summary_engine(instance)
        self._process_update_frequency(instance)
        self._process_translation_options(instance)

        if commit:
            instance.save()

        return instance
    
class FilterForm(forms.ModelForm):
    FIELD_CHOICES = (
        ("original_title", _("Original Title")),
        ("original_content", _("Original Content")),
        ("translated_title", _("Translated Title")),
        ("translated_content", _("Translated Content")),
    )
    keywords = TagField(required=True)
    class Meta:
        model = Filter
        fields = ("name","keywords","operation","target_field")

    target_field = forms.MultipleChoiceField(
        widget=CheckboxSelectMultiple,
        choices=FIELD_CHOICES,
        required=True,
        label=_("Target Field"),
    )

    def __init__(self, *args, **kwargs):
        super(FilterForm, self).__init__(*args, **kwargs)
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
    def save(self, commit=True):
        instance = super(FilterForm, self).save(commit=False)

        # 处理目标字段
        self._process_target_field(instance)

        if commit:
            instance.save()

        return instance
    

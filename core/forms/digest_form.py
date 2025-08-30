"""
日报表单
为Digest模型提供自定义表单功能
"""

from django import forms
from django.utils.translation import gettext_lazy as _
from core.models.digest import Digest


class DigestForm(forms.ModelForm):
    """日报配置表单"""
    
    # 自定义生成日期字段，使用多选框
    generation_weekdays = forms.MultipleChoiceField(
        choices=Digest.WEEKDAY_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label=_("生成日期"),
        help_text=_("选择在哪些星期生成日报 (可多选)")
    )
    
    class Meta:
        model = Digest
        fields = '__all__'
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # 如果是编辑现有实例，设置初始值
        if self.instance and self.instance.pk:
            if self.instance.generation_weekdays:
                # 确保初始值是列表格式
                weekdays = self.instance.generation_weekdays if isinstance(self.instance.generation_weekdays, list) else []
                self.fields['generation_weekdays'].initial = [str(day) for day in weekdays]
    
    def clean_generation_weekdays(self):
        """验证生成日期数据"""
        weekdays = self.cleaned_data.get('generation_weekdays', [])
        # 将字符串转换为整数
        try:
            return [int(day) for day in weekdays]
        except (ValueError, TypeError):
            raise forms.ValidationError(_("生成日期格式错误"))
    
    def save(self, commit=True):
        """保存表单数据"""
        instance = super().save(commit=False)
        
        # 处理生成日期数据
        generation_weekdays = self.cleaned_data.get('generation_weekdays', [])
        instance.generation_weekdays = generation_weekdays
        
        if commit:
            instance.save()
        
        return instance
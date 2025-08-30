import json
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from autoslug import AutoSlugField
from core.models.tag import Tag
from core.models.entry import Entry


class Digest(models.Model):
    """日报配置模型 - 定义日报的生成参数和设置"""
    
    # 星期数选择
    WEEKDAY_CHOICES = [
        (1, _('周一')),
        (2, _('周二')),
        (3, _('周三')),
        (4, _('周四')),
        (5, _('周五')),
        (6, _('周六')),
        (0, _('周日')),
    ]
    
    name = models.CharField(
        max_length=255, 
        verbose_name=_("名称"),
        help_text=_("日报名称")
    )
    
    description = models.TextField(
        verbose_name=_("描述"),
        blank=True,
        null=True,
        help_text=_("日报的详细描述")
    )
    
    slug = AutoSlugField(
        verbose_name=_("URL别名"),
        populate_from="name",
        unique=True,
        max_length=255,
    )
    
    tags = models.ManyToManyField(
        Tag,
        verbose_name=_("标签"),
        help_text=_("选择用于生成日报的标签"),
        related_name="digests"
    )
    
    # 日报生成日期配置
    generation_weekdays = models.JSONField(
        default=list,
        verbose_name=_("生成日期"),
        help_text=_("选择在哪些星期生成日报 (可多选)，格式: [1,2,3] 对应周一、周二、周三")
    )
    
    # 移除 generation_time 和 timezone 字段，统一使用 UTC 零点发布
    
    # 删除articles_per_day字段，改为AI自动决定
    
    # AI代理配置 - 用于文章生成
    agent_content_type = models.ForeignKey(
        ContentType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("AI代理类型"),
        help_text=_("用于生成文章的AI代理")
    )
    agent_object_id = models.PositiveIntegerField(
        null=True, 
        blank=True
    )
    agent = GenericForeignKey("agent_content_type", "agent_object_id")
    
    # 删除聚类配置，改为AI自主决定
    
    # 内容生成配置 - 移除content_modules字段，改为AI自主决定
    
    article_prompt = models.TextField(
        verbose_name=_("文章生成提示词"),
        blank=True,
        null=True,
        default="""You are a professional news analyst tasked with generating comprehensive analysis articles based on related news entries.

        Article Requirements:
        1. Title: Concise and powerful, reflecting the core theme
        2. Clear Structure: Include timeline, key viewpoints, in-depth analysis, and impact assessment
        3. Length: 200-300 words
        4. Language: {target_language}
        5. Style: Professional, objective, and insightful

        Please generate an article based on the following news entries:

        {articles_info}

        Output format as JSON:
        ```json
        {
        "title": "Article Title",
        "summary": "Article summary (within 50 words)",
        "content": "Complete article content (Markdown format)",
        "keywords": ["keyword1", "keyword2", "keyword3"],
        "reading_time": 2
        }
        ```""",
        help_text=_("用于指导AI生成文章的提示词模板")
    )
    
    system_prompt = models.TextField(
        verbose_name=_("系统提示词"),
        blank=True,
        null=True,
        default="""You are a professional content analyst and writer who excels at integrating multiple related news stories into high-quality analytical articles.

    Your articles have the following characteristics:
    - Accurately extract core information
    - Provide unique insights and analysis
    - Clear structure and rigorous logic
    - Concise language and powerful expression

    Your role:
    - Act as a senior journalist with extensive experience
    - Focus on delivering objective, data-driven analysis
    - Maintain professional standards while ensuring readability
    - Synthesize information from multiple sources effectively""",
        help_text=_("AI系统级别的指导提示词")
    )
    
    # 过滤配置 - 移除enable_tag_filters字段，默认启用标签过滤器
    
    # 状态信息
    is_active = models.BooleanField(
        verbose_name=_("是否激活"),
        default=True,
        help_text=_("是否启用此日报的自动生成")
    )
    
    last_generated = models.DateTimeField(
        verbose_name=_("最后生成时间"),
        null=True,
        blank=True,
        help_text=_("最后一次成功生成日报的时间")
    )
    
    total_tokens = models.PositiveIntegerField(
        verbose_name=_("总Token消耗"),
        default=0,
        help_text=_("累计消耗的Token数量")
    )
    
    created_at = models.DateTimeField(
        verbose_name=_("创建时间"),
        auto_now_add=True
    )
    
    updated_at = models.DateTimeField(
        verbose_name=_("更新时间"),
        auto_now=True
    )
    
    def __str__(self):
        return self.name
    
    def get_generation_weekdays_display(self):
        """获取生成日期的显示名称"""
        if not self.generation_weekdays:
            return "未设置"
        
        weekday_dict = dict(self.WEEKDAY_CHOICES)
        # 确保 generation_weekdays 是列表格式
        weekdays = self.generation_weekdays if isinstance(self.generation_weekdays, list) else []
        weekday_names = [weekday_dict.get(day, str(day)) for day in sorted(weekdays) if day in weekday_dict]
        return ", ".join(weekday_names)
    
    def is_generation_day(self, weekday=None):
        """判断今天是否是生成日期"""
        if weekday is None:
            weekday = timezone.now().weekday()  # 0=Monday, 6=Sunday
            # 转换为 Django 的格式 (0=Sunday, 1=Monday)
            weekday = 0 if weekday == 6 else weekday + 1
        
        # 确保 generation_weekdays 是列表格式
        weekdays = self.generation_weekdays if isinstance(self.generation_weekdays, list) else []
        return weekday in weekdays
    
    def clean(self):
        """模型数据验证"""
        super().clean()
        
        # 验证 generation_weekdays 字段
        if self.generation_weekdays is not None:
            if not isinstance(self.generation_weekdays, list):
                raise ValidationError({'generation_weekdays': '生成日期必须是列表格式'})
            
            valid_weekdays = [choice[0] for choice in self.WEEKDAY_CHOICES]
            invalid_weekdays = [day for day in self.generation_weekdays if day not in valid_weekdays]
            if invalid_weekdays:
                raise ValidationError({
                    'generation_weekdays': f'无效的星期数值: {invalid_weekdays}，有效值为: {valid_weekdays}'
                })
    
    # 移除get_content_modules方法，改为AI自主决定内容结构
    
    class Meta:
        verbose_name = _("日报配置")
        verbose_name_plural = _("日报配置")
        ordering = ["-created_at"]


class DigestArticle(models.Model):
    """日报文章模型 - 存储生成的文章内容"""
    
    STATUS_CHOICES = [
        ("draft", _("草稿")),
        ("published", _("已发布")),
        ("archived", _("已归档")),
    ]
    
    digest = models.ForeignKey(
        Digest,
        on_delete=models.CASCADE,
        related_name="articles",
        verbose_name=_("所属日报")
    )
    
    title = models.CharField(
        max_length=255,
        verbose_name=_("标题")
    )
    
    slug = AutoSlugField(
        verbose_name=_("URL别名"),
        populate_from="title",
        unique_with=["digest", "created_at__date"],
        max_length=255,
    )
    
    summary = models.TextField(
        verbose_name=_("摘要"),
        help_text=_("文章的简要摘要")
    )
    
    content = models.TextField(
        verbose_name=_("内容"),
        help_text=_("完整的文章内容，支持Markdown格式")
    )
    
    source_entries = models.ManyToManyField(
        Entry,
        verbose_name=_("来源文章"),
        help_text=_("生成此文章的原始Entry条目"),
        related_name="digest_articles"
    )
    
    cluster_id = models.IntegerField(
        verbose_name=_("聚类ID"),
        help_text=_("BERTopic算法生成的聚类标识符")
    )
    
    cluster_keywords = models.JSONField(
        verbose_name=_("聚类关键词"),
        default=list,
        help_text=_("该聚类的关键词列表")
    )
    
    quality_score = models.FloatField(
        verbose_name=_("质量评分"),
        default=0.0,
        help_text=_("文章质量评分 (0-1)")
    )
    

    tokens_used = models.PositiveIntegerField(
        verbose_name=_("Token使用量"),
        default=0,
        help_text=_("生成此文章消耗的Token数量")
    )
    
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="draft",
        verbose_name=_("状态")
    )
    
    published_at = models.DateTimeField(
        verbose_name=_("发布时间"),
        null=True,
        blank=True
    )
    
    created_at = models.DateTimeField(
        verbose_name=_("创建时间"),
        auto_now_add=True
    )
    
    updated_at = models.DateTimeField(
        verbose_name=_("更新时间"),
        auto_now=True
    )
    
    def __str__(self):
        return f"{self.digest.name} - {self.title}"
    
    def publish(self):
        """发布文章"""
        from datetime import datetime
        import pytz
        
        self.status = "published"
        # 设置发布时间为当日UTC零点
        utc_today = datetime.now(pytz.UTC).date()
        self.published_at = datetime.combine(utc_today, datetime.min.time()).replace(tzinfo=pytz.UTC)
        self.save()
    
    def get_source_links(self):
        """获取来源文章链接"""
        return [{"title": entry.original_title, "url": entry.link} 
                for entry in self.source_entries.all()]
    

    
    class Meta:
        verbose_name = _("日报文章")
        verbose_name_plural = _("日报文章")
        ordering = ["-created_at"]
        unique_together = ["digest", "slug"]



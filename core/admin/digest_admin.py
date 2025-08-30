"""
日报管理界面
提供日报配置的管理功能
"""

from django.contrib import admin
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from django.shortcuts import redirect
from django.urls import path, reverse
from django.contrib import messages
from django.http import JsonResponse
from django.template.response import TemplateResponse

from core.models.digest import Digest, DigestArticle
from core.admin.admin_site import core_admin_site
from core.forms.digest_form import DigestForm
from utils.digest_tasks import generate_daily_digest, get_digest_statistics



class DigestArticleInline(admin.TabularInline):
    """日报文章内联显示"""
    model = DigestArticle
    extra = 0
    readonly_fields = ['title', 'status', 'quality_score', 'published_at']
    fields = ['title', 'status', 'quality_score', 'published_at']
    
    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Digest, site=core_admin_site)
class DigestAdmin(admin.ModelAdmin):
    """日报配置管理"""
    
    form = DigestForm
    
    list_display = [
        'name', 'slug', 'is_active', 'generation_schedule_display', 
        'agent_info', 'tags_count', 
        'last_generated', 'total_tokens'
    ]
    
    list_filter = [
        'is_active', 'created_at'
    ]
    
    search_fields = ['name', 'slug']
    
    readonly_fields = [
        'slug', 'last_generated', 'total_tokens', 
        'created_at', 'updated_at', 'digest_statistics'
    ]
    
    filter_horizontal = ['tags']
    
    fieldsets = [
        (_('基本信息'), {
            'fields': ['name', 'description', 'slug', 'is_active']
        }),
        (_('生成配置'), {
            'fields': ['tags', 'generation_weekdays']
        }),
        (_('AI配置'), {
            'fields': ['agent_content_type', 'agent_object_id', 
                      'system_prompt', 'article_prompt']
        }),
        (_('统计信息'), {
            'fields': ['last_generated', 'total_tokens', 'created_at', 
                      'updated_at', 'digest_statistics'],
            'classes': ['collapse']
        })
    ]
    
    inlines = []
    
    actions = ['generate_digest_action', 'activate_digests', 'deactivate_digests']
    
    def agent_info(self, obj):
        """显示AI代理信息"""
        if obj.agent:
            return format_html(
                '<span style="color: green;">✓ {}</span>',
                obj.agent.name
            )
        return format_html('<span style="color: red;">✗ 未配置</span>')
    agent_info.short_description = _('AI代理')
    
    def generation_schedule_display(self, obj):
        """显示生成计划"""
        weekdays = obj.get_generation_weekdays_display()
        return format_html(
            '<div style="line-height: 1.2;">{}<br><small>00:00 (UTC)</small></div>',
            weekdays
        )
    generation_schedule_display.short_description = _('生成计划')
    
    def tags_count(self, obj):
        """显示标签数量"""
        count = obj.tags.count()
        if count > 0:
            tag_names = [tag.name for tag in obj.tags.all()[:3]]
            display_tags = ', '.join(tag_names)
            if count > 3:
                display_tags += f' (+{count-3})'
            return format_html(
                '<span title="{}">{} ({})</span>',
                display_tags, count, display_tags
            )
        return format_html('<span style="color: red;">0</span>')
    tags_count.short_description = _('标签数量')
    
    def digest_statistics(self, obj):
        """显示日报统计信息"""
        if obj.pk:
            try:
                stats = get_digest_statistics(obj.id, days=30)
                if stats['success']:
                    return format_html(
                        '''
                        <div style="line-height: 1.4;">
                        <strong>最近30天统计:</strong><br>
                        生成次数: {} (成功率: {:.1%})<br>
                        文章总数: {} (发布率: {:.1%})<br>
                        Token消耗: {:,}<br>
                        平均每次: {:.1f}篇文章
                        </div>
                        ''',
                        stats['total_generations'],
                        stats['success_rate'],
                        stats['total_articles'],
                        stats['publish_rate'],
                        stats['total_tokens_used'],
                        stats['avg_articles_per_generation']
                    )
                else:
                    return "统计信息获取失败"
            except Exception as e:
                return f"统计错误: {e}"
        return "保存后显示"
    digest_statistics.short_description = _('统计信息')
    
    def generate_digest_action(self, request, queryset):
        """批量生成日报"""
        generated_count = 0
        failed_count = 0
        
        for digest in queryset:
            try:
                result = generate_daily_digest(digest.id, force=True)
                if result['success']:
                    generated_count += 1
                else:
                    failed_count += 1
            except Exception as e:
                failed_count += 1
        
        if generated_count > 0:
            messages.success(
                request, 
                f'成功生成 {generated_count} 个日报'
            )
        if failed_count > 0:
            messages.error(
                request, 
                f'{failed_count} 个日报生成失败'
            )
    generate_digest_action.short_description = _('生成选中的日报')
    
    def activate_digests(self, request, queryset):
        """激活日报"""
        updated = queryset.update(is_active=True)
        messages.success(request, f'已激活 {updated} 个日报')
    activate_digests.short_description = _('激活选中的日报')
    
    def deactivate_digests(self, request, queryset):
        """停用日报"""
        updated = queryset.update(is_active=False)
        messages.success(request, f'已停用 {updated} 个日报')
    deactivate_digests.short_description = _('停用选中的日报')
    
    def get_urls(self):
        """添加自定义URL"""
        urls = super().get_urls()
        custom_urls = [
            path(
                '<int:digest_id>/generate/',
                self.admin_site.admin_view(self.generate_digest_view),
                name='digest_generate'
            ),
            path(
                '<int:digest_id>/statistics/',
                self.admin_site.admin_view(self.statistics_view),
                name='digest_statistics'
            ),
        ]
        return custom_urls + urls
    
    def generate_digest_view(self, request, digest_id):
        """生成单个日报的视图"""
        try:
            digest = Digest.objects.get(id=digest_id)
            
            if request.method == 'POST':
                force = request.POST.get('force', False)
                result = generate_daily_digest(digest_id, force=bool(force))
                
                return JsonResponse(result)
            
            return TemplateResponse(request, 'admin/digest_generate.html', {
                'digest': digest,
                'opts': self.model._meta,
                'has_view_permission': True,
            })
            
        except Digest.DoesNotExist:
            messages.error(request, '日报不存在')
            return redirect('admin:core_digest_changelist')
    
    def statistics_view(self, request, digest_id):
        """日报统计视图"""
        try:
            digest = Digest.objects.get(id=digest_id)
            days = int(request.GET.get('days', 30))
            
            stats = get_digest_statistics(digest_id, days)
            
            return TemplateResponse(request, 'admin/digest_statistics.html', {
                'digest': digest,
                'stats': stats,
                'days': days,
                'opts': self.model._meta,
                'has_view_permission': True,
            })
            
        except Digest.DoesNotExist:
            messages.error(request, '日报不存在')
            return redirect('admin:core_digest_changelist')


@admin.register(DigestArticle, site=core_admin_site)
class DigestArticleAdmin(admin.ModelAdmin):
    """日报文章管理"""
    
    list_display = [
        'title', 'digest', 'status', 'quality_score', 
        'tokens_used', 'published_at'
    ]
    
    list_filter = [
        'status', 'digest', 'created_at', 'published_at'
    ]
    
    search_fields = ['title', 'summary', 'content']
    
    readonly_fields = [
        'slug', 'cluster_id', 'cluster_keywords', 'quality_score',
        'tokens_used', 'created_at', 'updated_at',
        'source_entries_list'
    ]
    
    fieldsets = [
        (_('基本信息'), {
            'fields': ['digest', 'title', 'slug', 'summary', 'status']
        }),
        (_('内容'), {
            'fields': ['content']
        }),
        (_('聚类信息'), {
            'fields': ['cluster_id', 'cluster_keywords', 'quality_score'],
            'classes': ['collapse']
        }),
        (_('统计信息'), {
            'fields': ['tokens_used', 'published_at', 
                      'created_at', 'updated_at', 'source_entries_list'],
            'classes': ['collapse']
        })
    ]
    
    actions = ['publish_articles', 'unpublish_articles', 'archive_articles']
    
    def source_entries_list(self, obj):
        """显示来源文章列表"""
        if obj.pk:
            entries = obj.source_entries.all()[:10]  # 最多显示10个
            if entries:
                links = []
                for entry in entries:
                    title = entry.original_title or entry.translated_title or "无标题"
                    links.append(f'<a href="{entry.link}" target="_blank">{title[:50]}</a>')
                
                result = '<br>'.join(links)
                if obj.source_entries.count() > 10:
                    result += f'<br><em>... 还有 {obj.source_entries.count() - 10} 个</em>'
                
                return format_html(result)
            return "无来源文章"
        return "保存后显示"
    source_entries_list.short_description = _('来源文章')
    
    def publish_articles(self, request, queryset):
        """发布文章"""
        published_count = 0
        for article in queryset:
            if article.status != 'published':
                article.publish()
                published_count += 1
        
        messages.success(request, f'已发布 {published_count} 篇文章')
    publish_articles.short_description = _('发布选中的文章')
    
    def unpublish_articles(self, request, queryset):
        """取消发布文章"""
        updated = queryset.update(status='draft', published_at=None)
        messages.success(request, f'已取消发布 {updated} 篇文章')
    unpublish_articles.short_description = _('取消发布选中的文章')
    
    def archive_articles(self, request, queryset):
        """归档文章"""
        updated = queryset.update(status='archived')
        messages.success(request, f'已归档 {updated} 篇文章')
    archive_articles.short_description = _('归档选中的文章')



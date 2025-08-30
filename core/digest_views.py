"""
日报视图模块
提供日报的RSS和JSON订阅接口
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from django.http import HttpResponse, JsonResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.cache import cache_page
from django.views.decorators.http import require_http_methods
from django.conf import settings

from feedgen.feed import FeedGenerator
from core.models.digest import Digest, DigestArticle

logger = logging.getLogger(__name__)


@cache_page(60 * 15)  # 缓存15分钟
@require_http_methods(["GET"])
def digest_rss(request, digest_slug: str):
    """
    生成日报的RSS订阅
    """
    try:
        # 获取日报配置
        digest = get_object_or_404(Digest, slug=digest_slug, is_active=True)
        
        # 获取最近的文章（默认最近7天）
        days_back = int(request.GET.get('days', 7))
        days_back = min(days_back, 30)  # 最多30天
        
        since_date = timezone.now() - timedelta(days=days_back)
        articles = DigestArticle.objects.filter(
            digest=digest,
            status='published',
            published_at__gte=since_date
        ).order_by('-published_at')[:50]  # 最多50篇
        
        # 创建RSS Feed
        fg = FeedGenerator()
        fg.title(f"{digest.name} - 智能日报")
        fg.description(digest.description or f"{digest.name}的AI生成日报")
        fg.link(href=request.build_absolute_uri(), rel='alternate')
        fg.language('zh-CN')
        fg.id(request.build_absolute_uri())
        
        # 设置RSS元数据
        fg.lastBuildDate(timezone.now())
        fg.managingEditor(f"admin@{request.get_host()}")
        fg.webMaster(f"admin@{request.get_host()}")
        fg.generator("RSS-Translator Digest System")
        
        # 添加文章条目
        for article in articles:
            fe = fg.add_entry()
            fe.id(f"{request.build_absolute_uri()}/article/{article.id}")
            fe.title(article.title)
            fe.description(article.summary)
            fe.content(content=article.content, type="html")
            fe.link(href=f"{request.build_absolute_uri()}/article/{article.id}")
            
            if article.published_at:
                fe.pubDate(article.published_at)
            
            # 添加分类标签
            if article.cluster_keywords:
                for keyword in article.cluster_keywords:
                    fe.category(term=keyword)
            
            # 添加作者信息
            fe.author(name="AI日报系统", email=f"digest@{request.get_host()}")
            
            # 添加自定义字段
            fe.content(f"""
            <h3>文章摘要</h3>
            <p>{article.summary}</p>
            
            <h3>详细内容</h3>
            <div>{article.content}</div>
            
            <h3>来源文章</h3>
            <ul>
            {"".join(f'<li><a href="{link["url"]}">{link["title"]}</a></li>' 
                    for link in article.get_source_links())}
            </ul>
            
            <p><small>
            关键词: {", ".join(article.cluster_keywords)}
            </small></p>
            """, type="html")
        
        # 生成RSS XML
        rss_str = fg.rss_str(pretty=True)
        
        response = HttpResponse(rss_str, content_type='application/rss+xml; charset=utf-8')
        response['Content-Length'] = len(rss_str)
        
        return response
        
    except Exception as e:
        logger.error(f"生成RSS失败: {e}")
        return HttpResponse("RSS生成失败", status=500)


@cache_page(60 * 15)  # 缓存15分钟
@require_http_methods(["GET"])
def digest_json(request, digest_slug: str):
    """
    生成日报的JSON订阅
    """
    try:
        # 获取日报配置
        digest = get_object_or_404(Digest, slug=digest_slug, is_active=True)
        
        # 获取最近的文章
        days_back = int(request.GET.get('days', 7))
        days_back = min(days_back, 30)  # 最多30天
        
        since_date = timezone.now() - timedelta(days=days_back)
        articles = DigestArticle.objects.filter(
            digest=digest,
            status='published',
            published_at__gte=since_date
        ).select_related('digest').prefetch_related('source_entries').order_by('-published_at')[:50]
        
        # 构建JSON响应
        feed_data = {
            "version": "https://jsonfeed.org/version/1",
            "title": f"{digest.name} - 智能日报",
            "description": digest.description or f"{digest.name}的AI生成日报",
            "home_page_url": request.build_absolute_uri('/'),
            "feed_url": request.build_absolute_uri(),
            "language": "zh-CN",
            "favicon": f"{request.scheme}://{request.get_host()}/favicon.ico",
            "items": []
        }
        
        for article in articles:
            item = {
                "id": str(article.id),
                "title": article.title,
                "summary": article.summary,
                "content_html": article.content,
                "url": f"{request.build_absolute_uri()}/article/{article.id}",
                "date_published": article.published_at.isoformat() if article.published_at else None,
                "date_modified": article.updated_at.isoformat(),
                "author": {
                    "name": "AI日报系统",
                    "url": f"{request.scheme}://{request.get_host()}"
                },
                "tags": article.cluster_keywords,
                "_digest": {
                    "cluster_id": article.cluster_id,
                    "quality_score": article.quality_score,
                    "tokens_used": article.tokens_used,
                    "source_articles": [
                        {
                            "title": entry.original_title or entry.translated_title,
                            "url": entry.link,
                            "source": entry.feed.name if entry.feed else "未知来源",
                            "published": entry.pubdate.isoformat() if entry.pubdate else None
                        }
                        for entry in article.source_entries.all()[:5]  # 最多显示5个来源
                    ]
                }
            }
            feed_data["items"].append(item)
        
        response = JsonResponse(feed_data, json_dumps_params={'ensure_ascii': False, 'indent': 2})
        response['Content-Type'] = 'application/json; charset=utf-8'
        
        return response
        
    except Exception as e:
        logger.error(f"生成JSON失败: {e}")
        return JsonResponse({"error": "JSON生成失败"}, status=500)


@cache_page(60 * 60)  # 缓存1小时
@require_http_methods(["GET"])
def digest_list(request):
    """
    获取所有可用的日报列表
    """
    try:
        digests = Digest.objects.filter(is_active=True).order_by('name')
        
        digest_list = []
        for digest in digests:
            # 简化统计，不再依赖DigestGeneration
            
            recent_articles_count = digest.articles.filter(
                created_at__gte=timezone.now() - timedelta(days=7),
                status='published'
            ).count()
            
            digest_info = {
                "id": digest.id,
                "name": digest.name,
                "slug": digest.slug,
                "description": digest.description,
                "generation_time": "00:00",  # 固定为UTC零点
                "generation_weekdays": digest.get_generation_weekdays_display(),
                "last_generated": digest.last_generated.isoformat() if digest.last_generated else None,
                "recent_articles_count": recent_articles_count,
                "rss_url": request.build_absolute_uri(f"/digest/rss/{digest.slug}"),
                "json_url": request.build_absolute_uri(f"/digest/json/{digest.slug}"),
                "tags": [tag.name for tag in digest.tags.all()[:5]]  # 最多显示5个标签
            }
            

            
            digest_list.append(digest_info)
        
        return JsonResponse({
            "digests": digest_list,
            "total_count": len(digest_list),
            "generated_at": timezone.now().isoformat()
        }, json_dumps_params={'ensure_ascii': False, 'indent': 2})
        
    except Exception as e:
        logger.error(f"获取日报列表失败: {e}")
        return JsonResponse({"error": "获取日报列表失败"}, status=500)


@cache_page(60 * 30)  # 缓存30分钟
@require_http_methods(["GET"])
def digest_article_detail(request, article_id: int):
    """
    获取单篇日报文章详情
    """
    try:
        article = get_object_or_404(
            DigestArticle.objects.select_related('digest').prefetch_related('source_entries'),
            id=article_id,
            status='published'
        )
        
        article_data = {
            "id": article.id,
            "title": article.title,
            "summary": article.summary,
            "content": article.content,
            "digest": {
                "name": article.digest.name,
                "slug": article.digest.slug
            },
            "cluster_id": article.cluster_id,
            "cluster_keywords": article.cluster_keywords,
            "quality_score": article.quality_score,
            "published_at": article.published_at.isoformat() if article.published_at else None,
            "updated_at": article.updated_at.isoformat(),
            "source_articles": [
                {
                    "title": entry.original_title or entry.translated_title,
                    "url": entry.link,
                    "source": entry.feed.name if entry.feed else "未知来源",
                    "published": entry.pubdate.isoformat() if entry.pubdate else None,
                    "summary": entry.ai_summary or entry.original_summary
                }
                for entry in article.source_entries.all()
            ]
        }
        
        return JsonResponse(article_data, json_dumps_params={'ensure_ascii': False, 'indent': 2})
        
    except Exception as e:
        logger.error(f"获取文章详情失败: {e}")
        return JsonResponse({"error": "获取文章详情失败"}, status=500)


@require_http_methods(["GET"])
def digest_status(request, digest_slug: str):
    """
    获取日报生成状态
    """
    try:
        digest = get_object_or_404(Digest, slug=digest_slug, is_active=True)
        
        # 简化状态查询，不再依赖DigestGeneration
        status_data = {
            "digest": {
                "name": digest.name,
                "slug": digest.slug,
                "generation_time": "00:00",  # 固定为UTC零点
                "generation_weekdays": digest.get_generation_weekdays_display(),
                "is_active": digest.is_active
            },
            "today": {
                "generated": False,  # 简化为False，可根据需要调整
                "status": None,
                "articles_count": 0,
                "error_message": None
            },
            "recent_generations": [],  # 简化为空列表
            "next_generation": {
                "expected_time": "00:00",  # 固定为UTC零点
                "weekdays": digest.get_generation_weekdays_display(),
                "next_run": "00:00 (UTC)"
            }
        }
        
        return JsonResponse(status_data, json_dumps_params={'ensure_ascii': False, 'indent': 2})
        
    except Exception as e:
        logger.error(f"获取日报状态失败: {e}")
        return JsonResponse({"error": "获取日报状态失败"}, status=500)
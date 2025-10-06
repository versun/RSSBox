import logging
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.core.files.uploadedfile import InMemoryUploadedFile
from lxml import etree
from django.utils.translation import gettext_lazy as _
from django.db.models import Q

from .models import Feed, Tag, Entry, Digest, Filter, OpenAIAgent, DeepLAgent, LibreTranslateAgent

logger = logging.getLogger(__name__)


# Feeds API Views
def feeds_list(request):
    """Get all feeds with optional filtering"""
    status_filter = request.GET.get('status')
    tag_filter = request.GET.get('tag')

    feeds = Feed.objects.all().order_by('-last_fetch')

    if status_filter:
        if status_filter == 'active':
            feeds = feeds.filter(fetch_status=True)
        elif status_filter == 'error':
            feeds = feeds.filter(fetch_status=False)

    if tag_filter:
        feeds = feeds.filter(tags__name=tag_filter)

    return render(request, 'frontend/feeds_list.html', {'feeds': feeds})


def feed_create(request):
    """Create feed form"""
    return render(request, 'frontend/feed_form.html', {'feed': None})


def feed_detail(request, pk):
    """Get feed detail"""
    feed = get_object_or_404(Feed, pk=pk)
    return JsonResponse({
        'id': feed.id,
        'name': feed.name,
        'feed_url': feed.feed_url,
        'update_frequency': feed.update_frequency,
        'max_posts': feed.max_posts,
        'fetch_article': feed.fetch_article,
        'target_language': feed.target_language,
        'translate_title': feed.translate_title,
        'translate_content': feed.translate_content,
        'summary': feed.summary,
        'translation_display': feed.translation_display,
        'tags': list(feed.tags.values_list('name', flat=True)),
        'last_fetch': feed.last_fetch.isoformat() if feed.last_fetch else None,
        'fetch_status': feed.fetch_status,
    })


def feed_edit(request, pk):
    """Edit feed form"""
    feed = get_object_or_404(Feed, pk=pk)
    return render(request, 'frontend/feed_form.html', {'feed': feed})


@require_http_methods(["DELETE"])
def feed_delete(request, pk):
    """Delete feed"""
    feed = get_object_or_404(Feed, pk=pk)
    feed.delete()
    return HttpResponse(status=204)


def feed_entries(request, pk):
    """Get entries for a specific feed"""
    feed = get_object_or_404(Feed, pk=pk)
    entries = feed.entries.all().order_by('-pubdate')[:50]  # Limit to 50 entries

    return render(request, 'frontend/entries_list.html', {
        'entries': entries,
        'feed': feed
    })


# Tags API Views
def tags_list(request):
    """Get all tags"""
    tags = Tag.objects.all()
    return render(request, 'frontend/tags_list.html', {'tags': tags})


def tag_create(request):
    """Create tag form"""
    return render(request, 'frontend/tag_form.html', {'tag': None})


def tag_detail(request, pk):
    """Get tag detail"""
    tag = get_object_or_404(Tag, pk=pk)
    return JsonResponse({
        'id': tag.id,
        'name': tag.name,
        'slug': tag.slug,
    })


def tag_edit(request, pk):
    """Edit tag form"""
    tag = get_object_or_404(Tag, pk=pk)
    return render(request, 'frontend/tag_form.html', {'tag': tag})


@require_http_methods(["DELETE"])
def tag_delete(request, pk):
    """Delete tag"""
    tag = get_object_or_404(Tag, pk=pk)
    tag.delete()
    return HttpResponse(status=204)


# Entries API Views
def entries_list(request):
    """Get all entries with optional filtering"""
    search_query = request.GET.get('q')
    tag_filter = request.GET.get('tag')
    feed_filter = request.GET.get('feed')

    entries = Entry.objects.select_related('feed').order_by('-pubdate')

    if search_query:
        entries = entries.filter(
            Q(original_title__icontains=search_query) |
            Q(translated_title__icontains=search_query) |
            Q(original_content__icontains=search_query) |
            Q(translated_content__icontains=search_query)
        )

    if tag_filter:
        entries = entries.filter(feed__tags__name=tag_filter)

    if feed_filter:
        entries = entries.filter(feed_id=feed_filter)

    # Paginate - get first 100 entries
    entries = entries[:100]

    return render(request, 'frontend/entries_list.html', {'entries': entries})


def entry_detail(request, pk):
    """Get entry detail"""
    entry = get_object_or_404(Entry, pk=pk)
    return JsonResponse({
        'id': entry.id,
        'feed_name': entry.feed.name,
        'original_title': entry.original_title,
        'translated_title': entry.translated_title,
        'original_content': entry.original_content,
        'translated_content': entry.translated_content,
        'original_summary': entry.original_summary,
        'ai_summary': entry.ai_summary,
        'link': entry.link,
        'author': entry.author,
        'pubdate': entry.pubdate.isoformat() if entry.pubdate else None,
        'updated': entry.updated.isoformat() if entry.updated else None,
    })


# Digests API Views
def digests_list(request):
    """Get all digests"""
    digests = Digest.objects.all().order_by('-last_generated')
    return render(request, 'frontend/digests_list.html', {'digests': digests})


def digest_create(request):
    """Create digest form"""
    return render(request, 'frontend/digest_form.html', {'digest': None})


def digest_detail(request, pk):
    """Get digest detail"""
    digest = get_object_or_404(Digest, pk=pk)
    return JsonResponse({
        'id': digest.id,
        'name': digest.name,
        'slug': digest.slug,
        'days_range': digest.days_range,
        'tags': list(digest.tags.values_list('name', flat=True)),
        'last_generated': digest.last_generated.isoformat() if digest.last_generated else None,
    })


def digest_edit(request, pk):
    """Edit digest form"""
    digest = get_object_or_404(Digest, pk=pk)
    return render(request, 'frontend/digest_form.html', {'digest': digest})


@require_http_methods(["DELETE"])
def digest_delete(request, pk):
    """Delete digest"""
    digest = get_object_or_404(Digest, pk=pk)
    digest.delete()
    return HttpResponse(status=204)


# Import OPML
def import_opml_form(request):
    """Import OPML form"""
    return render(request, 'frontend/import_opml.html')


@csrf_exempt
def import_opml_process(request):
    """Process OPML import"""
    if request.method == "POST":
        opml_file = request.FILES.get("opml_file")
        if opml_file and isinstance(opml_file, InMemoryUploadedFile):
            try:
                # 直接读取字节数据（lxml 支持二进制解析）
                opml_content = opml_file.read()

                # 使用安全的 lxml 解析器解析 OPML
                parser = etree.XMLParser(resolve_entities=False)
                root = etree.fromstring(opml_content, parser=parser)
                body = root.find("body")

                if body is None:
                    messages.error(request, _("Invalid OPML: Missing body element"))
                    return redirect("admin:core_feed_changelist")

                # 递归处理所有 outline 节点
                def process_outlines(outlines, tag: str = None):
                    for outline in outlines:
                        # 检查是否为 feed（有 xmlUrl 属性）
                        if "xmlUrl" in outline.attrib:
                            feed, created = Feed.objects.get_or_create(
                                feed_url=outline.get("xmlUrl"),
                                defaults={
                                    "name": outline.get("title") or outline.get("text")
                                },
                            )
                            if tag:
                                tag_obj, _ = Tag.objects.get_or_create(name=tag)
                                feed.tags.add(tag_obj)
                        # 处理嵌套结构（新类别）
                        elif outline.find("outline") is not None:
                            new_tag = outline.get("text") or outline.get("title")
                            process_outlines(outline.findall("outline"), new_tag)

                # 从 body 开始处理顶级 outline
                process_outlines(body.findall("outline"))

                messages.success(request, _("OPML file imported successfully."))
            except etree.XMLSyntaxError as e:
                messages.error(request, _("XML syntax error: {}").format(str(e)))
            except Exception as e:
                messages.error(
                    request, _("Error importing OPML file: {}").format(str(e))
                )
        else:
            messages.error(request, _("Please upload a valid OPML file."))

    return render(request, 'frontend/import_opml.html')


# Filters API Views
def filters_list(request):
    """Get all filters"""
    filters = Filter.objects.all().order_by('-id')
    return render(request, 'frontend/filters_list.html', {'filters': filters})


def filter_create(request):
    """Create filter form"""
    return render(request, 'frontend/filter_form.html', {'filter': None})


def filter_detail(request, pk):
    """Get filter detail"""
    filter_obj = get_object_or_404(Filter, pk=pk)
    return JsonResponse({
        'id': filter_obj.id,
        'name': filter_obj.name,
        'keywords': list(filter_obj.keywords.values_list('name', flat=True)),
        'agent_id': filter_obj.agent_id,
        'filter_prompt': filter_obj.filter_prompt,
        'filter_method': filter_obj.filter_method,
        'operation': filter_obj.operation,
        'filter_original_title': filter_obj.filter_original_title,
        'filter_original_content': filter_obj.filter_original_content,
        'filter_translated_title': filter_obj.filter_translated_title,
        'filter_translated_content': filter_obj.filter_translated_content,
        'total_tokens': filter_obj.total_tokens,
    })


def filter_edit(request, pk):
    """Edit filter form"""
    filter_obj = get_object_or_404(Filter, pk=pk)
    return render(request, 'frontend/filter_form.html', {'filter': filter_obj})


@require_http_methods(["DELETE"])
def filter_delete(request, pk):
    """Delete filter"""
    filter_obj = get_object_or_404(Filter, pk=pk)
    filter_obj.delete()
    return HttpResponse(status=204)


def filter_test(request, pk):
    """Test filter"""
    filter_obj = get_object_or_404(Filter, pk=pk)
    # Get recent entries for testing
    recent_entries = Entry.objects.select_related('feed').order_by('-pubdate')[:5]

    results = []
    for entry in recent_entries:
        try:
            # Apply filter to this entry
            filtered_queryset = filter_obj.apply_filter(Entry.objects.filter(id=entry.id))
            passed = filtered_queryset.exists()
            results.append({
                'entry_title': entry.original_title[:50] + '...' if len(entry.original_title) > 50 else entry.original_title,
                'passed': passed,
                'reason': '通过过滤器' if passed else '被过滤器拦截'
            })
        except Exception as e:
            results.append({
                'entry_title': entry.original_title[:50] + '...' if len(entry.original_title) > 50 else entry.original_title,
                'passed': False,
                'reason': f'过滤器错误: {str(e)}'
            })

    return JsonResponse({
        'filter_name': filter_obj.name or '未命名过滤器',
        'results': results,
        'total_tested': len(results),
        'total_passed': sum(1 for r in results if r['passed'])
    })


# Agents API Views
def agents_list(request):
    """Get all agents"""
    openai_agents = OpenAIAgent.objects.all()
    deepl_agents = DeepLAgent.objects.all()
    libretranslate_agents = LibreTranslateAgent.objects.all()

    return render(request, 'frontend/agents_list.html', {
        'openai_agents': openai_agents,
        'deepl_agents': deepl_agents,
        'libretranslate_agents': libretranslate_agents
    })


def agent_create(request):
    """Create agent form"""
    agent_type = request.GET.get('type', 'openai')
    return render(request, 'frontend/agent_form.html', {
        'agent': None,
        'agent_type': agent_type
    })


def agent_detail(request, pk):
    """Get agent detail"""
    # Try to find agent in different models
    agent = None
    agent_type = 'unknown'

    try:
        agent = OpenAIAgent.objects.get(pk=pk)
        agent_type = 'openai'
    except OpenAIAgent.DoesNotExist:
        pass

    if agent is None:
        try:
            agent = DeepLAgent.objects.get(pk=pk)
            agent_type = 'deepl'
        except DeepLAgent.DoesNotExist:
            pass

    if agent is None:
        try:
            agent = LibreTranslateAgent.objects.get(pk=pk)
            agent_type = 'libretranslate'
        except LibreTranslateAgent.DoesNotExist:
            pass

    if agent is None:
        return JsonResponse({'error': 'Agent not found'}, status=404)

    return JsonResponse({
        'id': agent.id,
        'name': agent.name,
        'type': agent_type,
        'valid': agent.valid,
        'log': agent.log,
    })


def agent_edit(request, pk):
    """Edit agent form"""
    # Try to find agent in different models
    agent = None
    agent_type = 'unknown'

    try:
        agent = OpenAIAgent.objects.get(pk=pk)
        agent_type = 'openai'
    except OpenAIAgent.DoesNotExist:
        pass

    if agent is None:
        try:
            agent = DeepLAgent.objects.get(pk=pk)
            agent_type = 'deepl'
        except DeepLAgent.DoesNotExist:
            pass

    if agent is None:
        try:
            agent = LibreTranslateAgent.objects.get(pk=pk)
            agent_type = 'libretranslate'
        except LibreTranslateAgent.DoesNotExist:
            pass

    if agent is None:
        return JsonResponse({'error': 'Agent not found'}, status=404)

    return render(request, 'frontend/agent_form.html', {
        'agent': agent,
        'agent_type': agent_type
    })


@require_http_methods(["DELETE"])
def agent_delete(request, pk):
    """Delete agent"""
    # Try to find and delete agent in different models
    deleted = False

    try:
        agent = OpenAIAgent.objects.get(pk=pk)
        agent.delete()
        deleted = True
    except OpenAIAgent.DoesNotExist:
        pass

    if not deleted:
        try:
            agent = DeepLAgent.objects.get(pk=pk)
            agent.delete()
            deleted = True
        except DeepLAgent.DoesNotExist:
            pass

    if not deleted:
        try:
            agent = LibreTranslateAgent.objects.get(pk=pk)
            agent.delete()
            deleted = True
        except LibreTranslateAgent.DoesNotExist:
            pass

    if not deleted:
        return JsonResponse({'error': 'Agent not found'}, status=404)

    return HttpResponse(status=204)


def agent_test(request, pk):
    """Test agent"""
    # Try to find agent in different models
    agent = None
    agent_type = 'unknown'

    try:
        agent = OpenAIAgent.objects.get(pk=pk)
        agent_type = 'openai'
    except OpenAIAgent.DoesNotExist:
        pass

    if agent is None:
        try:
            agent = DeepLAgent.objects.get(pk=pk)
            agent_type = 'deepl'
        except DeepLAgent.DoesNotExist:
            pass

    if agent is None:
        try:
            agent = LibreTranslateAgent.objects.get(pk=pk)
            agent_type = 'libretranslate'
        except LibreTranslateAgent.DoesNotExist:
            pass

    if agent is None:
        return JsonResponse({'error': 'Agent not found'}, status=404)

    # Test the agent
    test_result = {
        'agent_name': agent.name,
        'agent_type': agent_type,
        'valid': False,
        'message': 'Unknown error'
    }

    try:
        valid = agent.validate()
        test_result['valid'] = valid
        if valid:
            test_result['message'] = '代理连接正常'
        else:
            test_result['message'] = '代理连接失败'
    except Exception as e:
        test_result['message'] = f'测试失败: {str(e)}'

    return JsonResponse(test_result)


# Form Processing Views
from django.views.decorators.http import require_POST
from django.urls import reverse


@require_POST
def tag_create_process(request):
    """Process tag creation"""
    try:
        name = request.POST.get('name')
        if not name:
            messages.error(request, "标签名称不能为空")
            return render(request, 'frontend/tag_form.html', {'tag': None})

        from .models import Tag
        tag = Tag.objects.create(name=name)
        messages.success(request, f"标签 '{tag.name}' 创建成功")
        return redirect('core_frontend:tags')
    except Exception as e:
        messages.error(request, f"创建标签失败: {str(e)}")
        return render(request, 'frontend/tag_form.html', {'tag': None})


@require_POST
def tag_update_process(request, pk):
    """Process tag update"""
    try:
        tag = get_object_or_404(Tag, pk=pk)
        name = request.POST.get('name')

        if not name:
            messages.error(request, "标签名称不能为空")
            return render(request, 'frontend/tag_form.html', {'tag': tag})

        tag.name = name
        tag.save()
        messages.success(request, f"标签 '{tag.name}' 更新成功")
        return redirect('core_frontend:tags')
    except Exception as e:
        messages.error(request, f"更新标签失败: {str(e)}")
        return render(request, 'frontend/tag_form.html', {'tag': get_object_or_404(Tag, pk=pk)})


@require_POST
def digest_create_process(request):
    """Process digest creation"""
    try:
        name = request.POST.get('name')
        days_range = int(request.POST.get('days_range', 1))
        description = request.POST.get('description', '')
        target_language = request.POST.get('target_language')
        summarizer_id = request.POST.get('summarizer')
        prompt = request.POST.get('prompt')
        is_active = request.POST.get('is_active') == 'on'

        if not all([name, target_language, summarizer_id]):
            messages.error(request, "请填写所有必需字段")
            return render(request, 'frontend/digest_form.html', {'digest': None})

        from .models import Digest, OpenAIAgent
        summarizer = get_object_or_404(OpenAIAgent, pk=summarizer_id)

        digest = Digest.objects.create(
            name=name,
            days_range=days_range,
            description=description,
            target_language=target_language,
            summarizer=summarizer,
            prompt=prompt,
            is_active=is_active
        )

        messages.success(request, f"摘要 '{digest.name}' 创建成功")
        return redirect('core_frontend:digests')
    except Exception as e:
        messages.error(request, f"创建摘要失败: {str(e)}")
        return render(request, 'frontend/digest_form.html', {'digest': None})


@require_POST
def digest_update_process(request, pk):
    """Process digest update"""
    try:
        digest = get_object_or_404(Digest, pk=pk)

        digest.name = request.POST.get('name')
        digest.days_range = int(request.POST.get('days_range', 1))
        digest.description = request.POST.get('description', '')
        digest.target_language = request.POST.get('target_language')
        summarizer_id = request.POST.get('summarizer')
        digest.prompt = request.POST.get('prompt')
        digest.is_active = request.POST.get('is_active') == 'on'

        if not all([digest.name, digest.target_language, summarizer_id]):
            messages.error(request, "请填写所有必需字段")
            return render(request, 'frontend/digest_form.html', {'digest': digest})

        from .models import OpenAIAgent
        digest.summarizer = get_object_or_404(OpenAIAgent, pk=summarizer_id)
        digest.save()

        messages.success(request, f"摘要 '{digest.name}' 更新成功")
        return redirect('core_frontend:digests')
    except Exception as e:
        messages.error(request, f"更新摘要失败: {str(e)}")
        return render(request, 'frontend/digest_form.html', {'digest': get_object_or_404(Digest, pk=pk)})


@require_POST
def filter_create_process(request):
    """Process filter creation"""
    try:
        name = request.POST.get('name', '')
        keywords_str = request.POST.get('keywords', '')
        filter_method = int(request.POST.get('filter_method', 0))
        operation = request.POST.get('operation') == 'true'
        agent_id = request.POST.get('agent')
        filter_prompt = request.POST.get('filter_prompt', '')

        # Parse keywords
        keywords = []
        if keywords_str:
            keywords = [k.strip() for k in keywords_str.split(',') if k.strip()]

        from .models import Filter, OpenAIAgent
        filter_obj = Filter.objects.create(
            name=name,
            filter_method=filter_method,
            operation=operation,
            filter_prompt=filter_prompt,
            filter_original_title=request.POST.get('filter_original_title') == 'on',
            filter_original_content=request.POST.get('filter_original_content') == 'on',
            filter_translated_title=request.POST.get('filter_translated_title') == 'on',
            filter_translated_content=request.POST.get('filter_translated_content') == 'on'
        )

        # Set keywords
        if keywords:
            from .models import Tag
            for keyword in keywords:
                tag, created = Tag.objects.get_or_create(name=keyword)
                filter_obj.keywords.add(tag)

        # Set agent
        if agent_id:
            filter_obj.agent = get_object_or_404(OpenAIAgent, pk=agent_id)
            filter_obj.save()

        messages.success(request, f"过滤器 '{filter_obj.name or '未命名'}' 创建成功")
        return redirect('core_frontend:filters')
    except Exception as e:
        messages.error(request, f"创建过滤器失败: {str(e)}")
        return render(request, 'frontend/filter_form.html', {'filter': None})


@require_POST
def filter_update_process(request, pk):
    """Process filter update"""
    try:
        filter_obj = get_object_or_404(Filter, pk=pk)

        name = request.POST.get('name', '')
        keywords_str = request.POST.get('keywords', '')
        filter_method = int(request.POST.get('filter_method', 0))
        operation = request.POST.get('operation') == 'true'
        agent_id = request.POST.get('agent')
        filter_prompt = request.POST.get('filter_prompt', '')

        # Parse keywords
        keywords = []
        if keywords_str:
            keywords = [k.strip() for k in keywords_str.split(',') if k.strip()]

        # Update filter
        filter_obj.name = name
        filter_obj.filter_method = filter_method
        filter_obj.operation = operation
        filter_obj.filter_prompt = filter_prompt
        filter_obj.filter_original_title = request.POST.get('filter_original_title') == 'on'
        filter_obj.filter_original_content = request.POST.get('filter_original_content') == 'on'
        filter_obj.filter_translated_title = request.POST.get('filter_translated_title') == 'on'
        filter_obj.filter_translated_content = request.POST.get('filter_translated_content') == 'on'

        # Clear and set keywords
        filter_obj.keywords.clear()
        if keywords:
            from .models import Tag
            for keyword in keywords:
                tag, created = Tag.objects.get_or_create(name=keyword)
                filter_obj.keywords.add(tag)

        # Set agent
        if agent_id:
            from .models import OpenAIAgent
            filter_obj.agent = get_object_or_404(OpenAIAgent, pk=agent_id)
        else:
            filter_obj.agent = None

        filter_obj.save()

        messages.success(request, f"过滤器 '{filter_obj.name or '未命名'}' 更新成功")
        return redirect('core_frontend:filters')
    except Exception as e:
        messages.error(request, f"更新过滤器失败: {str(e)}")
        return render(request, 'frontend/filter_form.html', {'filter': get_object_or_404(Filter, pk=pk)})


@require_POST
def agent_create_process(request):
    """Process agent creation"""
    try:
        agent_type = request.POST.get('agent_type')
        name = request.POST.get('name')

        if not all([agent_type, name]):
            messages.error(request, "请填写所有必需字段")
            return render(request, 'frontend/agent_form.html', {'agent': None, 'agent_type': agent_type})

        if agent_type == 'openai':
            from .models import OpenAIAgent
            agent = OpenAIAgent.objects.create(
                name=name,
                api_key=request.POST.get('api_key'),
                model=request.POST.get('model', 'gpt-3.5-turbo'),
                base_url=request.POST.get('base_url', 'https://api.openai.com/v1'),
                rate_limit_rpm=int(request.POST.get('rate_limit_rpm', 0)),
                advanced_params=request.POST.get('advanced_params', '{}')
            )
        elif agent_type == 'deepl':
            from .models import DeepLAgent
            agent = DeepLAgent.objects.create(
                name=name,
                api_key=request.POST.get('api_key'),
                max_characters=int(request.POST.get('max_characters', 5000)),
                server_url=request.POST.get('server_url'),
                proxy=request.POST.get('proxy')
            )
        elif agent_type == 'libretranslate':
            from .models import LibreTranslateAgent
            agent = LibreTranslateAgent.objects.create(
                name=name,
                api_key=request.POST.get('api_key', ''),
                max_characters=int(request.POST.get('max_characters', 5000)),
                server_url=request.POST.get('server_url')
            )
        else:
            messages.error(request, "不支持的代理类型")
            return render(request, 'frontend/agent_form.html', {'agent': None, 'agent_type': agent_type})

        messages.success(request, f"AI代理 '{agent.name}' 创建成功")
        return redirect('core_frontend:agents')
    except Exception as e:
        messages.error(request, f"创建AI代理失败: {str(e)}")
        return render(request, 'frontend/agent_form.html', {'agent': None, 'agent_type': agent_type})


@require_POST
def agent_update_process(request, pk):
    """Process agent update"""
    try:
        # Find the agent
        agent = None
        agent_type = 'unknown'

        try:
            agent = OpenAIAgent.objects.get(pk=pk)
            agent_type = 'openai'
        except OpenAIAgent.DoesNotExist:
            pass

        if agent is None:
            try:
                agent = DeepLAgent.objects.get(pk=pk)
                agent_type = 'deepl'
            except DeepLAgent.DoesNotExist:
                pass

        if agent is None:
            try:
                agent = LibreTranslateAgent.objects.get(pk=pk)
                agent_type = 'libretranslate'
            except LibreTranslateAgent.DoesNotExist:
                pass

        if agent is None:
            messages.error(request, "代理不存在")
            return redirect('core_frontend:agents')

        # Update common fields
        agent.name = request.POST.get('name')

        # Update type-specific fields
        if agent_type == 'openai':
            agent.api_key = request.POST.get('api_key')
            agent.model = request.POST.get('model', 'gpt-3.5-turbo')
            agent.base_url = request.POST.get('base_url', 'https://api.openai.com/v1')
            agent.rate_limit_rpm = int(request.POST.get('rate_limit_rpm', 0))
            agent.advanced_params = request.POST.get('advanced_params', '{}')
        elif agent_type == 'deepl':
            agent.api_key = request.POST.get('api_key')
            agent.max_characters = int(request.POST.get('max_characters', 5000))
            agent.server_url = request.POST.get('server_url')
            agent.proxy = request.POST.get('proxy')
        elif agent_type == 'libretranslate':
            agent.api_key = request.POST.get('api_key', '')
            agent.max_characters = int(request.POST.get('max_characters', 5000))
            agent.server_url = request.POST.get('server_url')

        agent.save()

        messages.success(request, f"AI代理 '{agent.name}' 更新成功")
        return redirect('core_frontend:agents')
    except Exception as e:
        messages.error(request, f"更新AI代理失败: {str(e)}")
        return redirect('core_frontend:agents')

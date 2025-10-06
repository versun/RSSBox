from django.shortcuts import render
from django.db.models import Q
from .models import Feed, Entry, Tag, Digest, Filter, OpenAIAgent, DeepLAgent, LibreTranslateAgent, TestAgent


def dashboard(request):
    """Dashboard view"""
    # Get recent feeds and entries
    recent_feeds = Feed.objects.order_by('-last_fetch')[:5]
    recent_entries = Entry.objects.select_related('feed').order_by('-pubdate')[:10]

    context = {
        'recent_feeds': recent_feeds,
        'recent_entries': recent_entries,
        'total_feeds': Feed.objects.count(),
        'total_entries': Entry.objects.count(),
        'total_tags': Tag.objects.count(),
        'total_digests': Digest.objects.count(),
        'total_filters': Filter.objects.count(),
        'total_agents': OpenAIAgent.objects.count() + DeepLAgent.objects.count() + LibreTranslateAgent.objects.count() + TestAgent.objects.count(),
        'total_count': Entry.objects.count(),  # For global search counter
    }
    return render(request, 'frontend/dashboard.html', context)


def feeds(request):
    """Feeds view"""
    status_filter = request.GET.get('status')
    tag_filter = request.GET.get('tag')
    sort_by = request.GET.get('sort', '-last_fetch')

    feeds = Feed.objects.all().order_by(sort_by)

    if status_filter:
        if status_filter == 'active':
            feeds = feeds.filter(fetch_status=True)
        elif status_filter == 'error':
            feeds = feeds.filter(fetch_status=False)

    if tag_filter:
        feeds = feeds.filter(tags__name=tag_filter)

    context = {
        'feeds': feeds,
        'total_count': feeds.count(),
    }
    return render(request, 'frontend/feeds.html', context)


def entries(request):
    """Entries view"""
    search_query = request.GET.get('q')
    feed_filter = request.GET.get('feed')
    tag_filter = request.GET.get('tag')
    sort_by = request.GET.get('sort', '-pubdate')

    entries = Entry.objects.select_related('feed').order_by(sort_by)

    if search_query:
        entries = entries.filter(
            Q(original_title__icontains=search_query) |
            Q(translated_title__icontains=search_query) |
            Q(original_content__icontains=search_query) |
            Q(translated_content__icontains=search_query)
        )

    if feed_filter:
        entries = entries.filter(feed_id=feed_filter)

    if tag_filter:
        entries = entries.filter(feed__tags__name=tag_filter)

    # Get first 100 entries for display
    entries = entries[:100]

    context = {
        'entries': entries,
        'total_count': entries.count(),
    }
    return render(request, 'frontend/entries_list.html', context)


def tags(request):
    """Tags view"""
    tags = Tag.objects.all()
    context = {
        'tags': tags,
        'total_count': tags.count(),
    }
    return render(request, 'frontend/tags_list.html', context)


def digests(request):
    """Digests view"""
    digests = Digest.objects.all().order_by('-last_generated')
    context = {
        'digests': digests,
        'total_count': digests.count(),
    }
    return render(request, 'frontend/digests_list.html', context)


def filters(request):
    """Filters view"""
    filters = Filter.objects.all().order_by('-id')
    context = {
        'filters': filters,
        'total_count': filters.count(),
    }
    return render(request, 'frontend/filters_list.html', context)


def agents(request):
    """Agents view"""
    # Get all types of agents
    openai_agents = OpenAIAgent.objects.all()
    deepl_agents = DeepLAgent.objects.all()
    libretranslate_agents = LibreTranslateAgent.objects.all()
    test_agents = TestAgent.objects.all()

    context = {
        'openai_agents': openai_agents,
        'deepl_agents': deepl_agents,
        'libretranslate_agents': libretranslate_agents,
        'test_agents': test_agents,
        'total_count': openai_agents.count() + deepl_agents.count() + libretranslate_agents.count() + test_agents.count(),
    }
    return render(request, 'frontend/agents_list.html', context)

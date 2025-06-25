import logging
from django.core.cache import cache
from feed2json import feed2json
from django.http import HttpResponse, StreamingHttpResponse, JsonResponse
from utils.feed_action import merge_feeds_into_one_atom, generate_atom_feed

from .models import Feed

def make_response(atom_feed, filename, formate="xml"):
    if formate == "json":
        # 如果需要返回 JSON 格式
        feed_json = feed2json(atom_feed)
        response = JsonResponse(feed_json)
    else:
        # 如果需要返回 XML 格式
        response = StreamingHttpResponse(
            atom_feed, content_type="application/xml"
        )
        response["Content-Disposition"] = f"inline; filename={filename}.xml"
    return response

def cache_rss(feed_slug:str, feed_type="t", formate="xml"):
    logging.debug(f"Start cache_rss for {feed_slug} with type {feed_type} and format {formate}")
    # 生成唯一的缓存键
    cache_key = f'cache_rss_{feed_slug}_{feed_type}_{formate}'

    feed = Feed.objects.get(slug=feed_slug)
    atom_feed = generate_atom_feed(feed, feed_type)
    if not atom_feed:
        return HttpResponse(status=500, content="Feed not found, Maybe it's still in progress")
    
    response = make_response(atom_feed, feed_slug, formate)
    # 缓存
    cache.set(cache_key, response, None)
    logging.debug(f"Cached successfully with key {cache_key}")
    return response

def cache_category(category:str, feed_type="t", formate="xml"):
    logging.debug(f"Start cache_category for {category} with type {feed_type} and format {formate}")
    # 生成唯一的缓存键
    cache_key = f'cache_category_{category}_{feed_type}_{formate}'

    feeds = Feed.objects.filter(category=category)
    atom_feed = merge_feeds_into_one_atom(category, feeds, feed_type)

    if not atom_feed:
        return HttpResponse(status=500, content="Feed not found, Maybe it's still in progress")
    
    response = make_response(atom_feed, category, formate)
    # 缓存
    cache.set(cache_key, response, None)
    logging.debug(f"Cached successfully with key {cache_key}")
    return response


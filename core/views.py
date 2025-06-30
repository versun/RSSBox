import logging
from django.http import HttpResponse, StreamingHttpResponse, JsonResponse
from django.utils.encoding import smart_str
from django.core.cache import cache
from django.views.decorators.http import condition
from .models import Feed
from django.shortcuts import redirect
from django.contrib import messages
from django.core.files.uploadedfile import InMemoryUploadedFile
from lxml import etree
from django.utils.translation import gettext_lazy as _
from feed2json import feed2json

from .cache import cache_rss, cache_category


def _get_modified(request, feed_slug, feed_type="t", **kwargs):
    try:
        if feed_type == "t":
            modified = Feed.objects.get(slug=feed_slug).last_translate
        else:
            modified = Feed.objects.get(slug=feed_slug).last_fetch
    except Feed.DoesNotExist:
        logging.warning(
            "Translated feed not found, Maybe still in progress, Please confirm it's exist: %s",
            feed_slug,
        )
        modified = None
    return modified


def _get_etag(request, feed_slug, feed_type="t", **kwargs):
    try:
        if feed_type == "t":
            etag = Feed.objects.get(slug=feed_slug).last_translate
        else:
            etag = Feed.objects.get(slug=feed_slug).etag
    except Feed.DoesNotExist:
        logging.warning(
            "Feed not fetched yet, Please update it first: %s",
            feed_slug,
        )
        etag = None
    return etag


def _make_response(atom_feed, filename, format="xml"):
    if format == "json":
        # 如果需要返回 JSON 格式
        feed_json = feed2json(atom_feed)
        response = JsonResponse(feed_json)
    else:
        # 使用生成器函数实现流式传输
        def stream_content():
            chunk_size = 4096  # 每次发送4KB
            for i in range(0, len(atom_feed), chunk_size):
                yield atom_feed[i : i + chunk_size]

        response = StreamingHttpResponse(
            stream_content(),  # 使用生成器
            content_type="application/xml; charset=utf-8",
        )
        response["Content-Disposition"] = f"inline; filename={filename}.xml"
    return response


def import_opml(request):
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
                def process_outlines(outlines, category: str = None):
                    for outline in outlines:
                        # 检查是否为 feed（有 xmlUrl 属性）
                        if "xmlUrl" in outline.attrib:
                            Feed.objects.get_or_create(
                                name=outline.get("title") or outline.get("text"),
                                feed_url=outline.get("xmlUrl"),
                                category=category,
                            )
                        # 处理嵌套结构（新类别）
                        elif outline.find("outline") is not None:
                            new_category = outline.get("text") or outline.get("title")
                            process_outlines(outline.findall("outline"), new_category)

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

    return redirect("admin:core_feed_changelist")


@condition(etag_func=_get_etag, last_modified_func=_get_modified)
def rss(request, feed_slug, feed_type="t", format="xml"):
    # Sanitize the feed_slug to prevent path traversal attacks
    feed_slug = smart_str(feed_slug)
    try:
        cache_key = f"cache_rss_{feed_slug}_{feed_type}_{format}"
        content = cache.get(cache_key)
        if content is None:
            logging.debug(f"Cache MISS for key: {cache_key}")
            content = cache_rss(feed_slug, feed_type, format)
        else:
            logging.debug(f"Cache HIT for key: {cache_key}")

        return _make_response(content, feed_slug, format)
    except Exception as e:
        logging.warning(f"Feed not found {feed_slug}: {str(e)}")
        return HttpResponse(status=404, content="Feed not found, Maybe it's still in progress, Please try again later.")


def category(request, category: str, feed_type="t", format="xml"):
    category = smart_str(category)
    all_category = Feed.category.tag_model.objects.all()

    if category not in all_category:
        return HttpResponse(status=404)

    try:
        cache_key = f"cache_category_{category}_{feed_type}_{format}"
        content = cache.get(cache_key)
        if content is None:
            logging.debug(f"Cache MISS for key: {cache_key}")
            content = cache_category(category, feed_type, format)
        else:
            logging.debug(f"Cache HIT for key: {cache_key}")
        return _make_response(content, category, format)
    except Exception as e:
        logging.warning("Category not found: %s / %s", category, str(e))
        return HttpResponse(status=404, content="Feed not found, Maybe it's still in progress, Please try again later.")

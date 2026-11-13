import logging
from django.utils.http import content_disposition_header
from django.http import HttpResponse, StreamingHttpResponse, JsonResponse
from django.utils.encoding import smart_str
from django.utils import timezone
from django.views.decorators.http import condition
from .models import Feed, Tag, Digest
from django.shortcuts import redirect, get_object_or_404
from django.contrib import messages
from django.core.files.uploadedfile import InMemoryUploadedFile
from lxml import etree
from django.utils.translation import gettext_lazy as _
from feed2json import feed2json
import mistune

from .cache import cache_rss, cache_tag, cache_digest
from core.models.filter import cache_version

logger = logging.getLogger(__name__)


def _get_modified(request, feed_slug, feed_type="t", **kwargs):
    try:
        feed = Feed.objects.get(slug=feed_slug)
        if feed_type == "t":
            modified = feed.last_translate
        else:
            modified = feed.last_fetch
    except Feed.DoesNotExist:
        logger.warning(
            "Translated feed not found, Maybe still in progress, Please confirm it's exist: %s",
            feed_slug,
        )
        return None
    # Filter-result clears / batch reassignments change what the feed
    # outputs without touching last_translate or last_fetch. The cache
    # version captures those; mixing it into Last-Modified keeps the
    # validator honest instead of serving 304 for changed content.
    return _mix_version_into_stamp(modified, "rss", feed.pk)


def _mix_version_into_stamp(stamp, scope, pk):
    """Add the invalidation version (seconds) onto a timestamp validator."""
    version = cache_version(scope, pk)
    if not version:
        return stamp
    base = stamp or timezone.datetime(2000, 1, 1, tzinfo=timezone.UTC)
    return base + timezone.timedelta(seconds=version)


def _get_etag(request, feed_slug, feed_type="t", **kwargs):
    try:
        feed = Feed.objects.get(slug=feed_slug)
        version = cache_version("rss", feed.pk)
        if feed_type == "t":
            base = feed.last_translate.isoformat() if feed.last_translate else ""
        else:
            base = feed.etag or ""
        if not base and not version:
            return None
        return f"{base}-v{version}"
    except Feed.DoesNotExist:
        logger.warning(
            "Feed not fetched yet, Please update it first: %s",
            feed_slug,
        )
        return None


def _make_response(atom_feed, filename, format="xml"):
    if not atom_feed:
        # XML and JSON agree on 404: a 200 body that just contains
        # "<error>" can get cached by clients/CDNs as a valid feed.
        if format == "json":
            return JsonResponse({"error": "No feed data available"}, status=404)
        return HttpResponse(
            "No feed data available", status=404, content_type="text/plain"
        )
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
        response["Content-Disposition"] = content_disposition_header(
            as_attachment=False, filename=f"{filename}.xml"
        )
    return response


def _get_digest_modified(request, slug: str, **kwargs):
    try:
        digest = Digest.objects.get(slug=slug)
        return digest.last_generated
    except Digest.DoesNotExist:
        return None


def _get_digest_etag(request, slug: str, **kwargs):
    try:
        digest = Digest.objects.get(slug=slug)
        return digest.last_generated.isoformat() if digest.last_generated else None
    except Digest.DoesNotExist:
        return None


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

    return redirect("admin:core_feed_changelist")


@condition(etag_func=_get_etag, last_modified_func=_get_modified)
def rss(request, feed_slug, feed_type="t", format="xml"):
    # Sanitize the feed_slug to prevent path traversal attacks
    feed_slug = smart_str(feed_slug)
    try:
        # cache_rss does its own get-or-generate on the versioned key;
        # a pre-read here would only ever hit a pre-versioning stale key.
        content = cache_rss(feed_slug, feed_type, format)
        return _make_response(content, feed_slug, format)
    except Exception as e:
        logger.warning(f"Feed not found {feed_slug}: {str(e)}")
        return HttpResponse(
            status=404,
            content="Feed not found, Maybe it's still in progress, Please try again later.",
        )


def tag(request, tag: str, feed_type="t", format="xml"):
    tag = smart_str(tag)
    if not Tag.objects.filter(slug=tag).exists():
        return HttpResponse(status=404)

    try:
        content = cache_tag(tag, feed_type, format)
        return _make_response(content, tag, format)
    except Exception as e:
        logger.warning("tag not found: %s / %s", tag, str(e))
        return HttpResponse(
            status=404,
            content="Feed not found, Maybe it's still in progress, Please try again later.",
        )


def digest_view(request, slug):
    """Display digest content as HTML page."""
    digest = get_object_or_404(Digest, slug=slug)

    # 获取最新一条摘要 Entry
    digest_feed = digest.get_digest_feed()
    latest = digest_feed.entries.order_by("-pubdate", "-id").first()
    if not latest or not latest.ai_summary:
        return HttpResponse(
            status=404,
            content="No digest content available. Please generate the digest first.",
        )

    # Convert markdown to HTML
    # md = markdown.Markdown(extensions=['extra', 'codehilite', 'tables', 'toc'])
    html_content = mistune.html(latest.ai_summary)
    
    # Format last_generated time with timezone conversion
    if digest.last_generated:
        local_time = timezone.localtime(digest.last_generated)
        generated_time = local_time.strftime("%Y-%m-%d %H:%M:%S")
    else:
        generated_time = "Never"

    # Create HTML response
    html_response = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{digest.name}</title>
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
                line-height: 1.6;
                color: #333;
                max-width: 800px;
                margin: 0 auto;
                padding: 20px;
                background-color: #f5f5f5;
            }}
            .container {{
                background-color: white;
                padding: 30px;
                border-radius: 8px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            }}
            h1, h2, h3 {{
                color: #2c3e50;
            }}
            h1 {{
                border-bottom: 2px solid #3498db;
                padding-bottom: 10px;
            }}
            .meta {{
                color: #666;
                font-size: 14px;
                margin-bottom: 20px;
                padding: 10px;
                background-color: #f8f9fa;
                border-radius: 4px;
            }}
            a {{
                color: #3498db;
                text-decoration: none;
            }}
            a:hover {{
                text-decoration: underline;
            }}
            blockquote {{
                border-left: 4px solid #3498db;
                padding-left: 15px;
                margin-left: 0;
                color: #555;
            }}
            code {{
                background-color: #f4f4f4;
                padding: 2px 4px;
                border-radius: 3px;
                font-family: Consolas, Monaco, monospace;
            }}
            pre {{
                background-color: #f4f4f4;
                padding: 15px;
                border-radius: 5px;
                overflow-x: auto;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="meta">
                <strong>Generated:</strong> {generated_time}<br>
                <strong>Tags:</strong> {", ".join([tag.name for tag in digest.tags.all()])}<br>
                <strong>Days Range:</strong> {digest.days_range} days
            </div>
            <div class="content">
                {html_content}
            </div>
        </div>
    </body>
    </html>
    """

    return HttpResponse(html_response, content_type="text/html; charset=utf-8")


@condition(etag_func=_get_digest_etag, last_modified_func=_get_digest_modified)
def digest(request, slug, format="xml"):
    """Return digest as ATOM/JSON feed, with caching."""
    slug = smart_str(slug)
    try:
        content = cache_digest(slug, format)
        return _make_response(content, slug, format)
    except Exception as e:
        logger.warning(f"Digest not found {slug}: {str(e)}")
        return HttpResponse(
            status=404,
            content="Digest not found, or not generated yet.",
        )

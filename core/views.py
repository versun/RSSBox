import logging
from urllib.parse import quote
from django.utils.http import content_disposition_header
from django.http import HttpResponse, StreamingHttpResponse, JsonResponse
from django.utils.encoding import smart_str
from django.utils import timezone
from django.views.decorators.http import condition
from .models import Feed, Tag, Digest
from django.shortcuts import redirect, render, get_object_or_404
from django.contrib import messages
from django.core.files.uploadedfile import InMemoryUploadedFile
from lxml import etree
from django.utils.translation import gettext_lazy as _
from feed2json import feed2json
import mistune

from .cache import cache_rss, cache_tag, cache_digest
from .feed_preview import (
    PREVIEW_CSP,
    build_subscribe_url,
    render_atom_as_html,
    sanitize_entry_html,
)
from core.models.filter import cache_version

logger = logging.getLogger(__name__)


def _resolve_response_format(request, route_format="xml"):
    """Return ``"html"`` when an XML feed route is requested with ``?format=html``."""
    if (
        route_format == "xml"
        and request is not None
        and (request.GET.get("format") or "").strip().lower() == "html"
    ):
        return "html"
    return route_format


def _get_modified(request, feed_slug, feed_type="t", **kwargs):
    try:
        feed = Feed.objects.only("pk", "last_translate", "last_fetch").get(
            slug=feed_slug
        )
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


def _append_etag_suffix(etag: str, suffix: str) -> str:
    """Append a suffix inside a weak/quoted ETag so Django's quote_etag stays valid."""
    if etag.endswith('"') and (
        (etag.startswith('"') and len(etag) >= 2)
        or (etag.startswith('W/"') and len(etag) >= 4)
    ):
        return f'{etag[:-1]}{suffix}"'
    return f"{etag}{suffix}"


def _mix_version_into_etag(base: str, scope: str, pk):
    """Append the invalidation version onto an ETag base string."""
    version = cache_version(scope, pk)
    if not base and not version:
        return None
    return _append_etag_suffix(base, f"-v{version}") if version else base


def _get_etag(request, feed_slug, feed_type="t", format="xml", **kwargs):
    try:
        feed = Feed.objects.only("pk", "last_translate", "etag").get(slug=feed_slug)
        if feed_type == "t":
            base = feed.last_translate.isoformat() if feed.last_translate else ""
        else:
            base = feed.etag or ""
        etag = _mix_version_into_etag(base, "rss", feed.pk)
        if etag and _resolve_response_format(request, format) == "html":
            return _append_etag_suffix(etag, "-html")
        return etag
    except Feed.DoesNotExist:
        logger.warning(
            "Feed not fetched yet, Please update it first: %s",
            feed_slug,
        )
        return None


def _make_response(
    atom_feed,
    filename,
    format="xml",
    *,
    feed_url=None,
    subscribe_url=None,
    json_url=None,
):
    if not atom_feed:
        # XML, HTML, and JSON agree on 404: a 200 body that just contains
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
    elif format == "html":
        html = render_atom_as_html(
            atom_feed,
            feed_url=feed_url,
            subscribe_url=subscribe_url,
            json_url=json_url,
        )
        response = HttpResponse(html, content_type="text/html; charset=utf-8")
        response["Content-Security-Policy"] = PREVIEW_CSP
        response["Referrer-Policy"] = "no-referrer"
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
        digest = Digest.objects.only("pk", "last_generated").get(slug=slug)
    except Digest.DoesNotExist:
        return None
    if digest.last_generated is None:
        return None
    return _mix_version_into_stamp(digest.last_generated, "digest", digest.pk)


def _get_digest_etag(request, slug: str, format="xml", **kwargs):
    try:
        digest = Digest.objects.only("pk", "last_generated").get(slug=slug)
    except Digest.DoesNotExist:
        return None
    if digest.last_generated is None:
        return None
    base = digest.last_generated.isoformat()
    etag = _mix_version_into_etag(base, "digest", digest.pk)
    if etag and _resolve_response_format(request, format) == "html":
        return _append_etag_suffix(etag, "-html")
    return etag


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
    except Exception as e:
        logger.warning(f"Feed not found {feed_slug}: {str(e)}")
        return HttpResponse(
            status=404,
            content="Feed not found, Maybe it's still in progress, Please try again later.",
        )

    response_format = _resolve_response_format(request, format)
    quoted_slug = quote(feed_slug)
    if feed_type == "o":
        feed_url = f"/rss/proxy/{quoted_slug}"
        json_url = None
    else:
        feed_url = f"/rss/{quoted_slug}"
        json_url = f"/rss/json/{quoted_slug}"
    return _make_response(
        content,
        feed_slug,
        response_format,
        feed_url=feed_url,
        subscribe_url=build_subscribe_url(request, feed_url),
        json_url=json_url,
    )


def tag(request, tag: str, feed_type="t", format="xml"):
    tag = smart_str(tag)
    if not Tag.objects.filter(slug=tag).exists():
        return HttpResponse(status=404)

    try:
        content = cache_tag(tag, feed_type, format)
    except Exception as e:
        logger.warning("tag not found: %s / %s", tag, str(e))
        return HttpResponse(
            status=404,
            content="Feed not found, Maybe it's still in progress, Please try again later.",
        )

    response_format = _resolve_response_format(request, format)
    quoted_tag = quote(tag)
    if feed_type == "o":
        feed_url = f"/rss/tag/proxy/{quoted_tag}"
        json_url = None
    else:
        feed_url = f"/rss/tag/{quoted_tag}"
        json_url = f"/rss/tag/json/{quoted_tag}"
    return _make_response(
        content,
        tag,
        response_format,
        feed_url=feed_url,
        subscribe_url=build_subscribe_url(request, feed_url),
        json_url=json_url,
    )


def digest_view(request, slug, entry_id=None):
    """Display a digest as an HTML page.

    Without ``entry_id`` the latest digest is shown. With it, that one
    digest: each digest entry links here so that a reader which fetches
    the entry link (full-text scrapers) gets the digest it is showing,
    not whichever one is newest.
    """
    digest = get_object_or_404(Digest.objects.select_related("feed"), slug=slug)

    # find_digest_feed(): a GET must not create or update the feed row.
    # No feed yet means no digest yet: 404 below, for the per-entry page too.
    digest_feed = digest.find_digest_feed()
    if digest_feed is None:
        latest = None
    elif entry_id is not None:
        latest = get_object_or_404(digest_feed.entries, pk=entry_id)
    else:
        latest = digest_feed.entries.order_by("-pubdate", "-id").first()
    if not latest or not latest.ai_summary:
        return HttpResponse(
            status=404,
            content="No digest content available. Please generate the digest first.",
        )

    # Convert markdown to HTML
    # mistune passes raw HTML (<script>, onerror=...) through; sanitize it
    # like the feed preview does: the summary is model output.
    html_content = sanitize_entry_html(mistune.html(latest.ai_summary))

    # Show the date of the digest on the page. It can differ from
    # digest.last_generated, e.g. on an older entry's own page.
    generated_at = latest.pubdate or digest.last_generated
    if generated_at:
        local_time = timezone.localtime(generated_at)
        generated_time = local_time.strftime("%Y-%m-%d %H:%M:%S")
    else:
        generated_time = "Never"

    response = render(
        request,
        "rss/digest_view.html",
        {
            "digest": digest,
            "generated_time": generated_time,
            "tags": digest.tags.all(),
            "html_content": html_content,
        },
        content_type="text/html; charset=utf-8",
    )
    response["Content-Security-Policy"] = PREVIEW_CSP
    response["Referrer-Policy"] = "no-referrer"
    return response


@condition(etag_func=_get_digest_etag, last_modified_func=_get_digest_modified)
def digest(request, slug, format="xml"):
    """Return digest as ATOM/JSON feed (or HTML preview with ?format=html)."""
    slug = smart_str(slug)
    try:
        content = cache_digest(slug, format)
    except Exception as e:
        logger.warning(f"Digest not found {slug}: {str(e)}")
        return HttpResponse(
            status=404,
            content="Digest not found, or not generated yet.",
        )

    response_format = _resolve_response_format(request, format)
    quoted_slug = quote(slug)
    digest_feed_url = f"/rss/digest/{quoted_slug}"
    return _make_response(
        content,
        slug,
        response_format,
        feed_url=digest_feed_url,
        subscribe_url=build_subscribe_url(request, digest_feed_url),
        json_url=f"/rss/digest/json/{quoted_slug}",
    )

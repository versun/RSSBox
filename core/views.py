import logging
from django.http import HttpResponse
from django.utils.encoding import smart_str
from django.core.cache import cache
from django.views.decorators.http import condition
from .models import Feed, Tag
from django.shortcuts import redirect
from django.contrib import messages
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.utils.translation import gettext_lazy as _

from .cache import cache_rss, cache_tag
from core.services.opml import import_opml_content
from core.services.feed import build_feed_response

logger = logging.getLogger(__name__)


def _get_modified(request, feed_slug, feed_type="t", **kwargs):
    try:
        if feed_type == "t":
            modified = Feed.objects.get(slug=feed_slug).last_translate
        else:
            modified = Feed.objects.get(slug=feed_slug).last_fetch
    except Feed.DoesNotExist:
        logger.warning(
            "Translated feed not found, Maybe still in progress, Please confirm it's exist: %s",
            feed_slug,
        )
        modified = None
    return modified


def _get_etag(request, feed_slug, feed_type="t", **kwargs):
    try:
        if feed_type == "t":
            last_translate = Feed.objects.get(slug=feed_slug).last_translate
            etag = last_translate.isoformat() if last_translate else None
        else:
            etag = Feed.objects.get(slug=feed_slug).etag
    except Feed.DoesNotExist:
        logger.warning(
            "Feed not fetched yet, Please update it first: %s",
            feed_slug,
        )
        etag = None
    return etag


def import_opml(request):
    if request.method == "POST":
        opml_file = request.FILES.get("opml_file")
        if opml_file and isinstance(opml_file, InMemoryUploadedFile):
            try:
                import_opml_content(opml_file.read())
                messages.success(request, _("OPML file imported successfully."))
            except ValueError as e:
                messages.error(request, _(str(e)))
            except Exception as e:
                message = str(e)
                if "XMLSyntaxError" in type(e).__name__:
                    messages.error(request, _("XML syntax error: {}").format(message))
                else:
                    messages.error(
                        request, _("Error importing OPML file: {}").format(message)
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
            logger.debug(f"Cache MISS for key: {cache_key}")
            content = cache_rss(feed_slug, feed_type, format)
        else:
            logger.debug(f"Cache HIT for key: {cache_key}")

        return build_feed_response(content, feed_slug, format)
    except Exception as e:
        logger.warning(f"Feed not found {feed_slug}: {str(e)}")
        return HttpResponse(
            status=404,
            content="Feed not found, Maybe it's still in progress, Please try again later.",
        )


def tag(request, tag: str, feed_type="t", format="xml"):
    tag = smart_str(tag)
    all_tag = list(Tag.objects.values_list("slug", flat=True))

    if tag not in all_tag:
        return HttpResponse(status=404)

    try:
        cache_key = f"cache_tag_{tag}_{feed_type}_{format}"
        content = cache.get(cache_key)
        if content is None:
            logger.debug(f"Cache MISS for key: {cache_key}")
            content = cache_tag(tag, feed_type, format)
        else:
            logger.debug(f"Cache HIT for key: {cache_key}")
        return build_feed_response(content, tag, format)
    except Exception as e:
        logger.warning("tag not found: %s / %s", tag, str(e))
        return HttpResponse(
            status=404,
            content="Feed not found, Maybe it's still in progress, Please try again later.",
        )

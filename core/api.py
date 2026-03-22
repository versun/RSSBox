import hmac
import logging
from functools import partial
from typing import Any

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Prefetch
from django.utils import timezone
from ninja import NinjaAPI, Schema
from ninja.errors import HttpError
from pydantic import AnyHttpUrl, Field, TypeAdapter, field_validator

from core.cache import cache_rss, cache_tag
from core.management.commands.feed_updater import update_single_feed
from core.models import (
    DeepLAgent,
    Feed,
    LibreTranslateAgent,
    OpenAIAgent,
    Tag,
    TestAgent,
)
from core.tasks.task_manager import task_manager

logger = logging.getLogger(__name__)


class ExternalBearerAuth:
    def __call__(self, request):
        if not settings.EXTERNAL_API_ENABLED:
            raise HttpError(404, "Not found.")

        configured_token = settings.EXTERNAL_API_TOKEN
        if not configured_token:
            raise HttpError(503, "External API token is not configured.")

        authorization = request.headers.get("Authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HttpError(401, "Unauthorized.")

        if not hmac.compare_digest(token, configured_token):
            raise HttpError(401, "Unauthorized.")

        return token


external_api = NinjaAPI(
    auth=ExternalBearerAuth(),
    docs_url=None,
    openapi_url=None,
    urls_namespace="external_api",
)


MAX_SUPPORTED_UPDATE_FREQUENCY = 10080
FEED_NAME_MAX_LENGTH = Feed._meta.get_field("name").max_length
TAG_NAME_MAX_LENGTH = Tag._meta.get_field("name").max_length
FEED_CACHE_VARIANTS = (
    ("o", "xml"),
    ("o", "json"),
    ("t", "xml"),
    ("t", "json"),
)
ORIGINAL_FEED_CACHE_VARIANTS = tuple(
    variant for variant in FEED_CACHE_VARIANTS if variant[0] == "o"
)
TAG_CACHE_VARIANTS = (
    ("o", "xml"),
    ("t", "xml"),
    ("t", "json"),
)
ORIGINAL_TAG_CACHE_VARIANTS = tuple(
    variant for variant in TAG_CACHE_VARIANTS if variant[0] == "o"
)
VALID_TRANSLATOR_MODELS = (
    OpenAIAgent,
    DeepLAgent,
    LibreTranslateAgent,
    TestAgent,
)


def _allowed_target_languages() -> set[str]:
    return {language for language, _label in settings.TRANSLATION_LANGUAGES}


def _allowed_translation_display_values() -> set[int]:
    return {
        choice_value
        for choice_value, _label in Feed.TRANSLATION_DISPLAY_CHOICES
    }


def _normalize_non_blank_name(value: str | None) -> str | None:
    if value is None:
        return None

    normalized_value = value.strip()
    if not normalized_value:
        raise ValueError("name must not be blank.")

    return normalized_value


def _parse_translator_option(
    value: str | None,
) -> tuple[int | None, int | None]:
    if value is None:
        return None, None

    normalized_value = value.strip()
    if not normalized_value:
        raise ValueError("translator_option must not be blank.")

    try:
        content_type_part, object_id_part = normalized_value.split(":", 1)
        content_type_id = int(content_type_part)
        object_id = int(object_id_part)
    except ValueError as exc:
        raise ValueError(
            "translator_option must be '<content_type_id>:<object_id>'."
        ) from exc

    content_type = ContentType.objects.filter(id=content_type_id).first()
    model_class = content_type.model_class() if content_type else None
    if model_class not in VALID_TRANSLATOR_MODELS:
        raise ValueError("translator_option must reference a valid translator agent.")

    if not model_class.objects.filter(id=object_id, valid=True).exists():
        raise ValueError("translator_option must reference a valid translator agent.")

    return content_type_id, object_id


def _normalize_feed_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized_payload = dict(payload)
    if "translator_option" in normalized_payload:
        content_type_id, object_id = _parse_translator_option(
            normalized_payload.pop("translator_option")
        )
        normalized_payload["translator_content_type_id"] = content_type_id
        normalized_payload["translator_object_id"] = object_id
    return normalized_payload


def _feed_queryset():
    return (
        Feed.objects.exclude(author="RSSBox Digest")
        .prefetch_related(Prefetch("tags", queryset=Tag.objects.order_by("id")))
    )


def _feed_cache_keys(feed_slug: str) -> list[str]:
    return [
        f"cache_rss_{feed_slug}_{feed_type}_{format_type}"
        for feed_type, format_type in FEED_CACHE_VARIANTS
    ]


def _original_feed_cache_keys(feed_slug: str) -> list[str]:
    return [
        f"cache_rss_{feed_slug}_{feed_type}_{format_type}"
        for feed_type, format_type in ORIGINAL_FEED_CACHE_VARIANTS
    ]


def _tag_cache_keys(tag_slug: str) -> list[str]:
    return [
        f"cache_tag_{tag_slug}_{feed_type}_{format_type}"
        for feed_type, format_type in TAG_CACHE_VARIANTS
    ]


def _original_tag_cache_keys(tag_slug: str) -> list[str]:
    return [
        f"cache_tag_{tag_slug}_{feed_type}_{format_type}"
        for feed_type, format_type in ORIGINAL_TAG_CACHE_VARIANTS
    ]


def _invalidate_feed_caches(feed_slug: str | None) -> None:
    if not feed_slug:
        return
    _delete_cache_keys(_feed_cache_keys(feed_slug))


def _invalidate_original_feed_caches(feed_slug: str | None) -> None:
    if not feed_slug:
        return
    _delete_cache_keys(_original_feed_cache_keys(feed_slug))


def _invalidate_tag_caches(tag_slugs: set[str] | list[str]) -> None:
    cache_keys: list[str] = []
    for tag_slug in sorted(set(tag_slugs)):
        if not tag_slug:
            continue
        cache_keys.extend(_tag_cache_keys(tag_slug))
    _delete_cache_keys(cache_keys)


def _invalidate_original_tag_caches(tag_slugs: set[str] | list[str]) -> None:
    cache_keys: list[str] = []
    for tag_slug in sorted(set(tag_slugs)):
        if not tag_slug:
            continue
        cache_keys.extend(_original_tag_cache_keys(tag_slug))
    _delete_cache_keys(cache_keys)


def _delete_cache_keys(cache_keys: list[str]) -> None:
    if not cache_keys:
        return
    try:
        cache.delete_many(cache_keys)
    except Exception as exc:
        logger.warning("Failed to invalidate external API cache keys: %s", exc)


def _reset_feed_revalidation_state(
    feed: Feed,
    *,
    original_output_changed: bool,
    translated_output_changed: bool,
    translated_output_requires_reprocessing: bool,
) -> None:
    if original_output_changed:
        feed.last_fetch = None
        feed.etag = None
        feed.fetch_status = None
    if translated_output_requires_reprocessing:
        feed.last_translate = None
        feed.translation_status = None
    elif translated_output_changed and feed.translation_status is not None:
        feed.last_translate = timezone.now()


def _clear_feed_source_metadata(feed: Feed) -> None:
    feed.subtitle = None
    feed.link = None
    feed.author = None
    feed.language = None
    feed.pubdate = None
    feed.updated = None


def _validate_processing_requirements(feed: Feed, payload: dict[str, Any]) -> None:
    translate_title_enabled = payload.get("translate_title", feed.translate_title)
    translate_content_enabled = payload.get(
        "translate_content",
        feed.translate_content,
    )
    summary_enabled = payload.get("summary", feed.summary)

    if (
        "translator_content_type_id" in payload
        or "translator_object_id" in payload
    ):
        has_translator = bool(
            payload.get("translator_content_type_id")
            and payload.get("translator_object_id")
        )
    else:
        has_translator = bool(feed.translator)

    has_summarizer = (
        bool(payload.get("summarizer_id"))
        if "summarizer_id" in payload
        else bool(feed.summarizer_id)
    )

    if (translate_title_enabled or translate_content_enabled) and not has_translator:
        raise HttpError(
            422,
            "translator must already be configured before enabling translation.",
        )

    if summary_enabled and not has_summarizer:
        raise HttpError(
            422,
            "summarizer must already be configured before enabling summary.",
        )


def _translated_output_in_progress(feed: Feed) -> bool:
    return feed.translation_status is None and (
        feed.translate_title or feed.translate_content or feed.summary
    )


def _warm_feed_caches(feed: Feed) -> None:
    for feed_type, format_type in FEED_CACHE_VARIANTS:
        cache_rss(feed.slug, feed_type=feed_type, format=format_type)


def _warm_tag_caches(feed: Feed) -> None:
    for tag in feed.tags.all():
        for feed_type, format_type in TAG_CACHE_VARIANTS:
            cache_tag(tag.slug, feed_type=feed_type, format=format_type)


class TagSchema(Schema):
    id: int
    name: str
    slug: str


class FeedListItemSchema(Schema):
    id: int
    name: str | None
    feed_url: str
    slug: str | None
    target_language: str
    update_frequency: int
    max_posts: int
    fetch_article: bool
    translate_title: bool
    translate_content: bool
    summary: bool
    translation_display: int
    fetch_status: bool | None
    translation_status: bool | None
    last_fetch: Any = None
    last_translate: Any = None
    tags: list[TagSchema]


class FeedDetailSchema(FeedListItemSchema):
    subtitle: str | None
    link: str | None
    author: str | None
    language: str | None
    pubdate: Any = None
    updated: Any = None


class FeedCreateSchema(Schema):
    feed_url: str
    name: str | None = Field(default=None, max_length=FEED_NAME_MAX_LENGTH)
    target_language: str = None
    update_frequency: int = Field(default=None, ge=1)
    max_posts: int = Field(default=None, ge=1)
    fetch_article: bool = None
    translate_title: bool = None
    translate_content: bool = None
    summary: bool = None
    translation_display: int = None
    translator_option: str | None = None
    summarizer_id: int | None = Field(default=None, ge=1)

    @field_validator("feed_url")
    @classmethod
    def validate_feed_url(cls, value: str) -> str:
        TypeAdapter(AnyHttpUrl).validate_python(value)
        return value

    @field_validator("target_language")
    @classmethod
    def validate_target_language(cls, value: str | None) -> str | None:
        if value is not None and value not in _allowed_target_languages():
            raise ValueError("Unsupported target_language.")
        return value

    @field_validator("translation_display")
    @classmethod
    def validate_translation_display(cls, value: int | None) -> int | None:
        if value is not None and value not in _allowed_translation_display_values():
            raise ValueError("Unsupported translation_display.")
        return value

    @field_validator("update_frequency")
    @classmethod
    def validate_update_frequency(cls, value: int | None) -> int | None:
        if value is not None and value > MAX_SUPPORTED_UPDATE_FREQUENCY:
            raise ValueError(
                f"update_frequency must be <= {MAX_SUPPORTED_UPDATE_FREQUENCY}."
            )
        return value

    @field_validator("translator_option")
    @classmethod
    def validate_translator_option(cls, value: str | None) -> str | None:
        _parse_translator_option(value)
        return value

    @field_validator("summarizer_id")
    @classmethod
    def validate_summarizer_id(cls, value: int | None) -> int | None:
        if value is not None and not OpenAIAgent.objects.filter(
            id=value,
            valid=True,
        ).exists():
            raise ValueError("summarizer_id must reference a valid OpenAI agent.")
        return value


class FeedUpdateSchema(FeedCreateSchema):
    feed_url: str = None


class TagCreateSchema(Schema):
    name: str = Field(max_length=TAG_NAME_MAX_LENGTH)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _normalize_non_blank_name(value)


class TagUpdateSchema(Schema):
    name: str = Field(default=None, max_length=TAG_NAME_MAX_LENGTH)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        return _normalize_non_blank_name(value)


class FeedTagSetSchema(Schema):
    tag_ids: list[int]


class ActionStatusSchema(Schema):
    status: str
    detail: str


def _serialize_tag(tag: Tag) -> dict[str, Any]:
    return {
        "id": tag.id,
        "name": tag.name,
        "slug": tag.slug,
    }


def _serialize_feed(feed: Feed, *, detail: bool = False) -> dict[str, Any]:
    payload = {
        "id": feed.id,
        "name": feed.name,
        "feed_url": feed.feed_url,
        "slug": feed.slug,
        "target_language": feed.target_language,
        "update_frequency": feed.update_frequency,
        "max_posts": feed.max_posts,
        "fetch_article": feed.fetch_article,
        "translate_title": feed.translate_title,
        "translate_content": feed.translate_content,
        "summary": feed.summary,
        "translation_display": feed.translation_display,
        "fetch_status": feed.fetch_status,
        "translation_status": feed.translation_status,
        "last_fetch": feed.last_fetch,
        "last_translate": feed.last_translate,
        "tags": [_serialize_tag(tag) for tag in feed.tags.all()],
    }
    if detail:
        payload.update(
            {
                "subtitle": feed.subtitle,
                "link": feed.link,
                "author": feed.author,
                "language": feed.language,
                "pubdate": feed.pubdate,
                "updated": feed.updated,
            }
        )
    return payload


def _translated_cache_must_be_invalidated(
    feed: Feed,
    payload: dict[str, Any],
) -> bool:
    normalized_payload = _normalize_feed_payload(payload)

    feed_url_changed = (
        "feed_url" in normalized_payload
        and normalized_payload["feed_url"] != feed.feed_url
    )
    target_language_changed = (
        "target_language" in normalized_payload
        and normalized_payload["target_language"] != feed.target_language
    )
    fetch_article_changed = (
        "fetch_article" in normalized_payload
        and normalized_payload["fetch_article"] != feed.fetch_article
    )
    translator_changed = (
        (
            "translator_content_type_id" in normalized_payload
            or "translator_object_id" in normalized_payload
        )
        and (
            normalized_payload.get("translator_content_type_id")
            != feed.translator_content_type_id
            or normalized_payload.get("translator_object_id")
            != feed.translator_object_id
        )
    )
    summarizer_changed = (
        "summarizer_id" in normalized_payload
        and normalized_payload["summarizer_id"] != feed.summarizer_id
    )
    translate_title_disabled = (
        "translate_title" in normalized_payload
        and normalized_payload["translate_title"] is False
        and feed.translate_title
    )
    translate_content_disabled = (
        "translate_content" in normalized_payload
        and normalized_payload["translate_content"] is False
        and feed.translate_content
    )
    summary_disabled = (
        "summary" in normalized_payload
        and normalized_payload["summary"] is False
        and feed.summary
    )

    return any(
        (
            feed_url_changed,
            target_language_changed and (
                feed.translate_title or feed.translate_content or feed.summary
            ),
            translator_changed and (feed.translate_title or feed.translate_content),
            summarizer_changed and feed.summary,
            translate_title_disabled,
            translate_content_disabled,
            summary_disabled,
            fetch_article_changed and feed.translate_content,
        )
    )


def _get_feed_or_404(feed_id: int) -> Feed:
    try:
        return _feed_queryset().get(id=feed_id)
    except Feed.DoesNotExist as exc:
        raise HttpError(404, "Feed not found.") from exc


def _get_tag_or_404(tag_id: int) -> Tag:
    try:
        return Tag.objects.get(id=tag_id)
    except Tag.DoesNotExist as exc:
        raise HttpError(404, "Tag not found.") from exc


def _apply_feed_changes(feed: Feed, payload: dict[str, Any]) -> Feed:
    payload = _normalize_feed_payload(payload)
    _validate_processing_requirements(feed, payload)
    translate_content_enabled = payload.get(
        "translate_content",
        feed.translate_content,
    )
    feed_url_changed = "feed_url" in payload and payload["feed_url"] != feed.feed_url
    target_language_changed = (
        "target_language" in payload
        and payload["target_language"] != feed.target_language
    )
    fetch_article_changed = (
        "fetch_article" in payload and payload["fetch_article"] != feed.fetch_article
    )
    translator_changed = (
        (
            "translator_content_type_id" in payload
            or "translator_object_id" in payload
        )
        and (
            payload.get("translator_content_type_id")
            != feed.translator_content_type_id
            or payload.get("translator_object_id") != feed.translator_object_id
        )
    )
    summarizer_changed = (
        "summarizer_id" in payload and payload["summarizer_id"] != feed.summarizer_id
    )
    translate_title_disabled = (
        "translate_title" in payload
        and payload["translate_title"] is False
        and feed.translate_title
    )
    translate_content_disabled = (
        "translate_content" in payload
        and payload["translate_content"] is False
        and feed.translate_content
    )
    summary_disabled = (
        "summary" in payload and payload["summary"] is False and feed.summary
    )
    cleared_entry_fields: dict[str, Any] = {}
    if target_language_changed or translate_title_disabled or translator_changed:
        cleared_entry_fields["translated_title"] = None
    if (
        target_language_changed
        or translate_content_disabled
        or translator_changed
        or (fetch_article_changed and translate_content_enabled)
    ):
        cleared_entry_fields["translated_content"] = None
    if target_language_changed or summary_disabled or summarizer_changed:
        cleared_entry_fields["ai_summary"] = None
    original_output_changed = any(
        field_name in payload for field_name in ("feed_url", "name", "max_posts")
    )
    translated_output_requires_reprocessing = feed_url_changed or bool(
        cleared_entry_fields
    )
    translated_output_changed = (
        original_output_changed
        or "translation_display" in payload
        or translated_output_requires_reprocessing
    )

    for field_name, field_value in payload.items():
        setattr(feed, field_name, field_value)

    try:
        with transaction.atomic():
            _reset_feed_revalidation_state(
                feed,
                original_output_changed=original_output_changed,
                translated_output_changed=translated_output_changed,
                translated_output_requires_reprocessing=translated_output_requires_reprocessing,
            )
            if feed_url_changed:
                _clear_feed_source_metadata(feed)
            feed.save()

            if feed_url_changed:
                feed.entries.all().delete()
            elif cleared_entry_fields:
                feed.entries.update(**cleared_entry_fields)
    except IntegrityError as exc:
        if _is_duplicate_feed_integrity_error(exc):
            raise HttpError(
                409, "Feed with this feed_url and target_language already exists."
            ) from exc
        raise

    return _feed_queryset().get(id=feed.id)


def _is_duplicate_feed_integrity_error(exc: IntegrityError) -> bool:
    error_message = str(exc)
    error_message_lower = error_message.lower()
    return "unique_feed_lang" in error_message_lower or (
        "feed_url" in error_message_lower
        and "target_language" in error_message_lower
        and (
            "unique" in error_message_lower or "duplicate" in error_message_lower
        )
    )


def _submit_refresh(feed_id: int, feed_slug: str) -> None:
    task_manager.submit_task(
        f"update_feed_{feed_slug}",
        _refresh_feed_and_cache,
        feed_id,
    )


def _refresh_feed_and_cache(feed_id: int) -> None:
    try:
        feed = _feed_queryset().get(id=feed_id)
    except Feed.DoesNotExist:
        return

    update_single_feed(feed)
    try:
        refreshed_feed = _feed_queryset().get(id=feed_id)
    except Feed.DoesNotExist:
        return
    _warm_feed_caches(refreshed_feed)
    _warm_tag_caches(refreshed_feed)


@external_api.get("/feeds", response=list[FeedListItemSchema])
def list_feeds(request):
    feeds = _feed_queryset().order_by("id")
    return [_serialize_feed(feed) for feed in feeds]


@external_api.get("/feeds/{feed_id}", response=FeedDetailSchema)
def get_feed(request, feed_id: int):
    feed = _get_feed_or_404(feed_id)
    return _serialize_feed(feed, detail=True)


@external_api.post("/feeds", response={201: FeedDetailSchema})
def create_feed(request, payload: FeedCreateSchema):
    feed = Feed()
    feed = _apply_feed_changes(feed, payload.model_dump(exclude_unset=True))
    return 201, _serialize_feed(feed, detail=True)


@external_api.patch("/feeds/{feed_id}", response=FeedDetailSchema)
def update_feed(request, feed_id: int, payload: FeedUpdateSchema):
    feed = _get_feed_or_404(feed_id)
    tag_slugs = list(feed.tags.values_list("slug", flat=True))
    payload_data = payload.model_dump(exclude_unset=True)
    must_invalidate_translated_cache = _translated_cache_must_be_invalidated(
        feed,
        payload_data,
    )
    feed = _apply_feed_changes(feed, payload_data)
    if _translated_output_in_progress(feed) and not must_invalidate_translated_cache:
        _invalidate_original_feed_caches(feed.slug)
        _invalidate_original_tag_caches(tag_slugs)
    else:
        _invalidate_feed_caches(feed.slug)
        _invalidate_tag_caches(tag_slugs)
    return _serialize_feed(feed, detail=True)


@external_api.delete("/feeds/{feed_id}", response={204: None})
def delete_feed(request, feed_id: int):
    feed = _get_feed_or_404(feed_id)
    feed_slug = feed.slug
    tag_slugs = list(feed.tags.values_list("slug", flat=True))
    feed.delete()
    _invalidate_feed_caches(feed_slug)
    _invalidate_tag_caches(tag_slugs)
    return 204, None


@external_api.post("/feeds/{feed_id}/refresh", response={202: ActionStatusSchema})
def refresh_feed(request, feed_id: int):
    with transaction.atomic():
        feed = _get_feed_or_404(feed_id)
        feed.fetch_status = None
        feed.translation_status = None
        feed.save()
        transaction.on_commit(partial(_submit_refresh, feed.id, feed.slug))

    return 202, {
        "status": "queued",
        "detail": f"Refresh queued for feed {feed.id}.",
    }


@external_api.get("/tags", response=list[TagSchema])
def list_tags(request):
    return [_serialize_tag(tag) for tag in Tag.objects.order_by("id")]


@external_api.post("/tags", response={201: TagSchema})
def create_tag(request, payload: TagCreateSchema):
    tag = Tag.objects.create(name=payload.name)
    return 201, _serialize_tag(tag)


@external_api.patch("/tags/{tag_id}", response=TagSchema)
def update_tag(request, tag_id: int, payload: TagUpdateSchema):
    tag = _get_tag_or_404(tag_id)
    changes = payload.model_dump(exclude_unset=True)
    for field_name, field_value in changes.items():
        setattr(tag, field_name, field_value)
    tag.save()
    return _serialize_tag(tag)


@external_api.delete("/tags/{tag_id}", response={204: None})
def delete_tag(request, tag_id: int):
    tag = _get_tag_or_404(tag_id)
    tag.delete()
    return 204, None


@external_api.post("/feeds/{feed_id}/tags", response=FeedDetailSchema)
def set_feed_tags(request, feed_id: int, payload: FeedTagSetSchema):
    feed = _get_feed_or_404(feed_id)
    previous_tag_slugs = set(feed.tags.values_list("slug", flat=True))
    tags = list(Tag.objects.filter(id__in=payload.tag_ids).order_by("id"))
    found_tag_ids = {tag.id for tag in tags}
    missing_tag_ids = sorted(set(payload.tag_ids) - found_tag_ids)
    if missing_tag_ids:
        raise HttpError(404, f"Tag ids not found: {missing_tag_ids}")

    feed.tags.set(tags)
    current_tag_slugs = {tag.slug for tag in tags}
    _invalidate_tag_caches(previous_tag_slugs | current_tag_slugs)
    return _serialize_feed(_get_feed_or_404(feed_id), detail=True)

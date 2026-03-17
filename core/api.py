import hmac
from functools import partial
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction
from ninja import NinjaAPI, Schema
from ninja.errors import HttpError
from pydantic import AnyHttpUrl, Field, TypeAdapter, field_validator

from core.management.commands.feed_updater import update_single_feed
from core.models import Feed, Tag
from core.tasks.task_manager import task_manager


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


def _allowed_target_languages() -> set[str]:
    return {language for language, _label in settings.TRANSLATION_LANGUAGES}


def _allowed_translation_display_values() -> set[int]:
    return {
        choice_value
        for choice_value, _label in Feed.TRANSLATION_DISPLAY_CHOICES
    }


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
    name: str | None = None
    target_language: str = None
    update_frequency: int = Field(default=None, ge=1)
    max_posts: int = Field(default=None, ge=1)
    fetch_article: bool = None
    translate_title: bool = None
    translate_content: bool = None
    summary: bool = None
    translation_display: int = None

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


class FeedUpdateSchema(FeedCreateSchema):
    feed_url: str = None


class TagCreateSchema(Schema):
    name: str


class TagUpdateSchema(Schema):
    name: str = None


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
        "tags": [_serialize_tag(tag) for tag in feed.tags.all().order_by("id")],
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


def _get_feed_or_404(feed_id: int) -> Feed:
    try:
        return Feed.objects.prefetch_related("tags").get(id=feed_id)
    except Feed.DoesNotExist as exc:
        raise HttpError(404, "Feed not found.") from exc


def _get_tag_or_404(tag_id: int) -> Tag:
    try:
        return Tag.objects.get(id=tag_id)
    except Tag.DoesNotExist as exc:
        raise HttpError(404, "Tag not found.") from exc


def _apply_feed_changes(feed: Feed, payload: dict[str, Any]) -> Feed:
    feed_url_changed = "feed_url" in payload and payload["feed_url"] != feed.feed_url
    target_language_changed = (
        "target_language" in payload
        and payload["target_language"] != feed.target_language
    )

    for field_name, field_value in payload.items():
        setattr(feed, field_name, field_value)

    try:
        with transaction.atomic():
            if feed_url_changed or target_language_changed:
                feed.fetch_status = None
                feed.translation_status = None
            feed.save()

            if target_language_changed:
                feed.entries.update(
                    translated_content=None,
                    translated_title=None,
                    ai_summary=None,
                )

            if feed_url_changed:
                feed.entries.all().delete()
    except IntegrityError as exc:
        if _is_duplicate_feed_integrity_error(exc):
            raise HttpError(
                409, "Feed with this feed_url and target_language already exists."
            ) from exc
        raise

    return Feed.objects.prefetch_related("tags").get(id=feed.id)


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


def _submit_refresh(feed: Feed) -> None:
    task_manager.submit_task(
        f"update_feed_{feed.slug}",
        update_single_feed,
        feed,
    )


@external_api.get("/feeds", response=list[FeedListItemSchema])
def list_feeds(request):
    feeds = Feed.objects.prefetch_related("tags").order_by("id")
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
    feed = _apply_feed_changes(feed, payload.model_dump(exclude_unset=True))
    return _serialize_feed(feed, detail=True)


@external_api.delete("/feeds/{feed_id}", response={204: None})
def delete_feed(request, feed_id: int):
    feed = _get_feed_or_404(feed_id)
    feed.delete()
    return 204, None


@external_api.post("/feeds/{feed_id}/refresh", response={202: ActionStatusSchema})
def refresh_feed(request, feed_id: int):
    with transaction.atomic():
        feed = _get_feed_or_404(feed_id)
        feed.fetch_status = None
        feed.translation_status = None
        feed.save()
        transaction.on_commit(partial(_submit_refresh, feed))

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
    tags = list(Tag.objects.filter(id__in=payload.tag_ids).order_by("id"))
    found_tag_ids = {tag.id for tag in tags}
    missing_tag_ids = sorted(set(payload.tag_ids) - found_tag_ids)
    if missing_tag_ids:
        raise HttpError(404, f"Tag ids not found: {missing_tag_ids}")

    feed.tags.set(tags)
    return _serialize_feed(_get_feed_or_404(feed_id), detail=True)

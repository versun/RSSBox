import logging
import uuid
from django.utils import timezone
from django.db import transaction
from django.db.models import F
from bs4 import BeautifulSoup
import mistune
import newspaper

from core.models import Feed, Entry, Agent
from utils import text_handler
from core.tasks.fetch_feeds import FALLBACK_USER_AGENT, get_fetch_user_agent
from core.tasks.utils import (
    PERSISTENCE_ERRORS,
    PersistError,
    auto_retry,
    clear_entry_chunk_caches,
    get_entry_task_state_map,
    is_timeout_error,
    record_entry_stage_failure,
    record_entry_stage_success,
    record_spent_tokens,
    reset_entry_task_states,
    should_skip_entry_stage,
)

logger = logging.getLogger(__name__)


def handle_feeds_translation(feeds: list, target_field: str = "title"):
    for feed in feeds:
        try:
            if not feed.entries.exists():
                continue

            feed.translation_status = None
            feed.translation_started_at = timezone.now()
            feed.save(
                update_fields=["translation_status", "translation_started_at"]
            )
            logger.info(
                "Start translate %s of feed %s to %s",
                target_field,
                feed.feed_url,
                feed.target_language,
            )

            saved = translate_feed(feed, target_field=target_field)
            feed.translation_started_at = None
            if feed.translation_status or saved:
                # Bump on partial success too: last_translate drives the
                # translated feed's ETag/Last-Modified, so readers must see
                # the entries that did get translated.
                feed.last_translate = timezone.now()
            if feed.translation_status:
                feed.log += f"{timezone.now()} Translate Completed <br>"
            else:
                feed.log += f"{timezone.now()} Translation incomplete; failed entries remain pending <br>"
        except Exception as e:
            logger.error(f"Error in translate_feed for feed {feed.name}: {str(e)}")
            feed.translation_status = False
            feed.translation_started_at = None
            feed.log += f"{timezone.now()} {str(e)} <br>"
        finally:
            # Explicitly clean up reference
            del feed

    Feed.objects.bulk_update(
        feeds,
        fields=[
            "translation_status",
            "translation_started_at",
            "log",
            "last_translate",
        ],
    )
    # Clear the list after processing
    del feeds


def _entry_was_deleted(entry_pk, save_error) -> bool:
    """True only if the save failed because the row is gone (e.g. deleted by
    a concurrent admin "reprocess"); a query failure means the DB itself is
    unhealthy, in which case the feed should still abort."""
    # Django raises this when an update_fields save matches 0 rows.
    if "did not affect any rows" in str(save_error):
        return True
    try:
        return not Entry.objects.filter(pk=entry_pk).exists()
    except Exception:
        return False


def translate_feed(
    feed: Feed, target_field: str = "title", entry_ids: list | None = None
) -> int:
    """Translate feed entries; return how many entries were saved."""
    logger.info("Translating feed: %s", feed.target_language)
    total_tokens = 0
    total_characters = 0
    saved_entries = 0
    if not feed.translator:
        logger.error("Translate Engine Not Set for feed %s", feed.name)
        feed.log += f"{timezone.now()} Translate Engine Not Set<br>"
        feed.translation_status = False
        return 0
    feed.translation_status = True

    state_map = get_entry_task_state_map(feed, target_field, entry_ids=entry_ids)
    now = timezone.now()

    # 只处理前 feed.max_posts 条 entries; -id ties break equal pubdates
    # the same way cleanup_entries picks its keep-set.
    if entry_ids is not None:
        entries = feed.entries.filter(pk__in=entry_ids).order_by(
            "-pubdate", "-id"
        ).iterator(chunk_size=50)
    else:
        entries = feed.entries.order_by("-pubdate", "-id")[: feed.max_posts].iterator(
            chunk_size=50
        )

    for entry in entries:
        entry_tokens = 0
        entry_characters = 0
        entry_billed_cache_keys = []
        attempt_id = uuid.uuid4().hex[:12]
        try:
            logger.debug("Processing entry %s", entry.pk)
            needs_work = (
                target_field == "title"
                and feed.translate_title
                and not entry.translated_title
                and bool((entry.original_title or "").strip())
            ) or (
                target_field == "content"
                and feed.translate_content
                and not entry.translated_content
                and (bool(entry.original_content) or feed.fetch_article)
            )
            if not needs_work:
                continue

            entry_state = state_map.get(entry.pk)
            skip, skip_reason = should_skip_entry_stage(entry_state, now=now)
            if skip:
                if skip_reason == "backoff":
                    # Still failing and awaiting retry (not paused/isolated),
                    # so this feed's translation is not yet complete.
                    feed.translation_status = False
                logger.info(
                    "Skipping entry %s stage=%s (%s, attempts=%s, next_retry_at=%s)",
                    entry.pk,
                    target_field,
                    skip_reason,
                    entry_state.attempt_count,
                    entry_state.next_retry_at,
                )
                continue

            entry_needs_save = False

            # Process title translation
            if (
                target_field == "title"
                and feed.translate_title
                and not entry.translated_title
            ):
                metrics = _translate_entry_title(
                    entry=entry,
                    target_language=feed.target_language,
                    engine=feed.translator,
                    user_prompt=feed.additional_prompt,
                    attempt_id=attempt_id,
                )
                entry_tokens += metrics["tokens"]
                entry_characters += metrics["characters"]
                if metrics.get("billed_cache_keys"):
                    entry_billed_cache_keys.extend(metrics["billed_cache_keys"])
                if entry.translated_title:
                    entry_needs_save = True

            # Process content translation
            if target_field == "content" and feed.translate_content:
                # Only fetch while the entry still lacks both a translation and
                # a previously fetched full article. Using the explicit data
                # flag ``article_fetched`` (decoupled from EntryTaskState retry
                # counters) guarantees that:
                # - a failed web fetch is retried on the next attempt;
                # - enabling fetch_article later still fetches full text;
                # - a successful fetch (even before any LLM error or after
                #   force-update resets attempt_count) is never overwritten so
                #   cached chunks stay valid.
                if (
                    feed.fetch_article
                    and not entry.translated_content
                    and not entry.article_fetched
                ):
                    article_content = _fetch_article_content(entry.link)
                    if article_content:
                        entry.article_fetched = True
                        if article_content != entry.original_content:
                            entry.original_content = article_content
                            # Only the summary is derived from the body that
                            # just changed. ``translated_title`` depends on
                            # ``original_title`` alone and this pipeline never
                            # touches it: clearing it here dropped the title
                            # translation paid for earlier in the same run
                            # (issue #181).
                            entry.ai_summary = None
                            # Only a changed source counts as an update; translating
                            # must not overwrite the publisher's timestamp. The
                            # bumped ``updated`` is also what lets the AI filter
                            # re-evaluate this entry against its new content.
                            entry.updated = timezone.now()
                            fetch_update_fields = [
                                "original_content",
                                "updated",
                                "ai_summary",
                                "article_fetched",
                            ]
                        else:
                            fetch_update_fields = ["article_fetched"]
                        try:
                            entry.save(update_fields=fetch_update_fields)
                        except Exception as save_error:
                            if _entry_was_deleted(entry.pk, save_error):
                                logger.warning(
                                    "Entry %s was deleted while saving fetched article; skipping",
                                    entry.pk,
                                )
                                continue
                            raise PersistError(
                                f"Could not persist fetched content for entry {entry.pk}: {save_error}"
                            ) from save_error
                        if "original_content" in fetch_update_fields:
                            # Body changed: invalidate stale content chunk caches
                            # and reset summary state so summary will re-run.
                            clear_entry_chunk_caches([entry.pk], stages=["content"])
                            reset_entry_task_states(
                                [entry.pk], stages=["summary"], clear_chunks=True
                            )
                    # Clean up article content after use
                    del article_content

                if entry.original_content and not entry.translated_content:
                    metrics = _translate_entry_content(
                        entry=entry,
                        target_language=feed.target_language,
                        engine=feed.translator,
                        user_prompt=feed.additional_prompt,
                        attempt_id=attempt_id,
                    )
                    entry_tokens += metrics["tokens"]
                    entry_characters += metrics["characters"]
                    if metrics.get("billed_cache_keys"):
                        entry_billed_cache_keys.extend(metrics["billed_cache_keys"])
                    if entry.translated_content:
                        entry_needs_save = True

            if entry_needs_save:
                fields = [f"translated_{target_field}"]
                try:
                    with transaction.atomic():
                        entry.save(update_fields=fields)
                        if entry_tokens or entry_characters:
                            Feed.objects.filter(pk=feed.pk).update(
                                total_tokens=F("total_tokens") + entry_tokens,
                                total_characters=F("total_characters") + entry_characters,
                            )
                except Exception as save_error:
                    if _entry_was_deleted(entry.pk, save_error):
                        # The row vanished mid-run; the DB is fine, so bill
                        # the paid call and move on to the next entry.
                        if entry_tokens or entry_characters:
                            Feed.objects.filter(pk=feed.pk).update(
                                total_tokens=F("total_tokens") + entry_tokens,
                                total_characters=F("total_characters")
                                + entry_characters,
                            )
                            feed.total_tokens += entry_tokens
                            feed.total_characters += entry_characters
                        logger.warning(
                            "Entry %s was deleted during translation; skipping",
                            entry.pk,
                        )
                        continue
                    # Any other save failure aborts the feed (see handler
                    # below); the paid usage rides on the error for billing.
                    error = PersistError(
                        f"Could not persist translation for entry {entry.pk}: {save_error}"
                    )
                    error.tokens_used = entry_tokens
                    if entry_billed_cache_keys:
                        error.billed_cache_keys = tuple(
                            dict.fromkeys(entry_billed_cache_keys)
                        )
                    raise error from save_error
                record_entry_stage_success(entry, target_field)
                feed.total_tokens += entry_tokens
                feed.total_characters += entry_characters
                total_tokens += entry_tokens
                total_characters += entry_characters
                saved_entries += 1
        except PERSISTENCE_ERRORS as e:
            # The paid result could not be saved; further entries would hit
            # the same wall, so stop this feed instead of paying for more.
            record_spent_tokens(feed, e)
            logger.error(
                f"Aborting translation for feed {feed.name} at entry {entry.link}: {e}"
            )
            feed.log += f"{timezone.now()} Persistence failure, translation aborted at entry {entry.pk}: {e}<br>"
            feed.translation_status = False
            break
        except Exception as e:
            record_spent_tokens(feed, e)
            est_tokens = 0
            if is_timeout_error(e):
                est_tokens = getattr(e, "estimated_unbilled_tokens", 0)
                if not est_tokens:
                    source_text = (
                        entry.original_title
                        if target_field == "title"
                        else entry.original_content
                    )
                    try:
                        est_tokens = text_handler.get_token_count(source_text or "")
                    except Exception:
                        est_tokens = 0
            state = record_entry_stage_failure(
                entry,
                target_field,
                e,
                estimated_input_tokens=est_tokens,
            )
            logger.error(
                "Error processing entry %s (%s) stage=%s attempt_id=%s error_type=%s "
                "attempts=%s status=%s next_retry_at=%s: %s",
                entry.pk,
                entry.link,
                target_field,
                attempt_id,
                state.last_error_type if state else "unknown",
                state.attempt_count if state else None,
                state.status if state else None,
                state.next_retry_at if state else None,
                str(e),
            )
            feed.log += (
                f"{timezone.now()} Error processing entry {entry.link}: {str(e)}<br>"
            )
            feed.translation_status = False
        finally:
            # Explicitly clean up entry reference
            del entry

    logger.info(
        f"Translation completed. Tokens: {total_tokens}, Chars: {total_characters}"
    )
    # Clean up large variables
    del entries, total_tokens, total_characters
    return saved_entries


def _translate_entry_title(
    entry: Entry,
    target_language: str,
    engine: Agent,
    user_prompt: str = None,
    attempt_id: str | None = None,
) -> dict:
    """Translate entry title with memory optimization."""
    total_tokens = 0
    total_characters = 0
    billed_cache_keys = ()

    # Check if title needs translation
    if entry.translated_title or not (entry.original_title or "").strip():
        return {"tokens": 0, "characters": 0}

    logger.debug("[Title] Translating title")
    chunk_cache_context = {
        "entry_id": entry.pk,
        "stage": "title",
        "target_language": target_language,
    }
    if attempt_id:
        chunk_cache_context["attempt_id"] = attempt_id
    result = auto_retry(
        engine.translate,
        max_retries=3,
        require_text=True,
        text=entry.original_title,
        target_language=target_language,
        text_type="title",
        user_prompt=user_prompt,
        chunk_cache_context=chunk_cache_context,
    )

    if result:
        translated_title = result.get("text")
        entry.translated_title = translated_title if translated_title else None
        total_tokens = result.get("tokens", 0)
        total_characters = result.get("characters", 0)
        billed_cache_keys = tuple(result.get("billed_cache_keys") or ())

    metrics = {"tokens": total_tokens, "characters": total_characters}
    if billed_cache_keys:
        metrics["billed_cache_keys"] = billed_cache_keys
    return metrics


def _translate_entry_content(
    entry: Entry,
    target_language: str,
    engine: Agent,
    user_prompt: str = None,
    attempt_id: str | None = None,
) -> dict:
    """Translate entry content with memory optimization."""
    total_tokens = 0
    total_characters = 0

    # Check if content needs translation
    if entry.translated_content:
        return {"tokens": 0, "characters": 0}

    # Parse HTML with explicit cleanup
    soup = BeautifulSoup(entry.original_content, "lxml")

    # Add notranslate class to elements that shouldn't be translated
    for element in soup.find_all(string=True):
        if not element.get_text(strip=True):
            continue
        if text_handler.should_skip(element):
            parent = element.parent
            parent.attrs.update(
                {"class": parent.get("class", []) + ["notranslate"], "translate": "no"}
            )

    processed_html = str(soup)

    # Explicitly clean up BeautifulSoup object
    del soup
    import gc

    gc.collect()

    chunk_cache_context = {
        "entry_id": entry.pk,
        "stage": "content",
        "target_language": target_language,
    }
    if attempt_id:
        chunk_cache_context["attempt_id"] = attempt_id

    # Perform translation
    result = auto_retry(
        func=engine.translate,
        max_retries=3,
        require_text=True,
        text=processed_html,
        target_language=target_language,
        text_type="content",
        user_prompt=user_prompt,
        chunk_cache_context=chunk_cache_context,
    )

    translated_content = result.get("text")
    logger.info(f"Translated content: {translated_content[:100]}")
    entry.translated_content = translated_content if translated_content else None
    total_tokens = result.get("tokens", 0)
    total_characters = result.get("characters", 0)
    billed_cache_keys = tuple(result.get("billed_cache_keys") or ())

    # Clean up large strings
    del processed_html
    if translated_content:
        del translated_content

    metrics = {"tokens": total_tokens, "characters": total_characters}
    if billed_cache_keys:
        metrics["billed_cache_keys"] = billed_cache_keys
    return metrics


def _download_article(link: str, user_agent: str):
    """Download and parse one article with an explicit User-Agent."""
    config = newspaper.Config()
    config.browser_user_agent = user_agent
    article = newspaper.Article(link, config=config)
    article.download()
    article.parse()
    return article


def _fetch_article_content(link: str) -> str:
    """Fetch full article content with explicit cleanup."""
    content = ""
    article = None
    try:
        user_agent = get_fetch_user_agent()
        try:
            article = _download_article(link, user_agent)
        except Exception:
            # A WAF (e.g. Cloudflare) may block browser-like UAs: retry once
            # with the plain HTTP-client UA, mirroring the feed fetch fallback.
            if user_agent == FALLBACK_USER_AGENT:
                raise
            logger.warning(
                "Article download failed for %s, retrying with fallback UA", link
            )
            article = _download_article(link, FALLBACK_USER_AGENT)
        content = mistune.html(article.text)
    except Exception as e:
        logger.error(f"Article fetch failed: {link}: {str(e)}")
    finally:
        # Explicitly clean up newspaper objects
        if article is not None:
            del article
        return content

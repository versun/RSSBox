import logging
from django.utils import timezone
from django.db import transaction
from django.db.models import F
from bs4 import BeautifulSoup
import mistune
import newspaper

from core.models import Feed, Entry, Agent
from utils import text_handler
from core.tasks.fetch_feeds import FALLBACK_USER_AGENT, get_fetch_user_agent
from core.tasks.utils import auto_retry
from core.tasks.youtube_transcripts import processing_content, uses_youtube_transcript

logger = logging.getLogger(__name__)


def handle_feeds_translation(feeds: list, target_field: str = "title"):
    for feed in feeds:
        try:
            if not feed.entries.exists():
                continue

            feed.translation_status = None
            feed.save(update_fields=["translation_status"])
            logger.info(
                "Start translate %s of feed %s to %s",
                target_field,
                feed.feed_url,
                feed.target_language,
            )

            translate_feed(feed, target_field=target_field)
            if feed.translation_status:
                feed.last_translate = timezone.now()
                feed.log += f"{timezone.now()} Translate Completed <br>"
            else:
                feed.log += f"{timezone.now()} Translation incomplete; failed entries remain pending <br>"
        except Exception as e:
            logger.error(f"Error in translate_feed for feed {feed.name}: {str(e)}")
            feed.translation_status = False
            feed.log += f"{timezone.now()} {str(e)} <br>"
        finally:
            # Explicitly clean up reference
            del feed

    Feed.objects.bulk_update(
        feeds,
        fields=[
            "translation_status",
            "log",
            "last_translate",
        ],
    )
    # Clear the list after processing
    del feeds


def translate_feed(feed: Feed, target_field: str = "title"):
    """Translate and summarize feed entries with memory optimizations."""
    logger.info("Translating feed: %s", feed.target_language)
    total_tokens = 0
    total_characters = 0
    feed.translation_status = True

    # 只处理前 feed.max_posts 条 entries
    entries = feed.entries.order_by("-pubdate")[: feed.max_posts].iterator(
        chunk_size=50
    )

    for entry in entries:
        entry_tokens = 0
        entry_characters = 0
        try:
            logger.debug("Processing entry %s", entry.pk)
            if not feed.translator:
                raise Exception("Translate Engine Not Set")

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
                )
                entry_tokens += metrics["tokens"]
                entry_characters += metrics["characters"]
                entry_needs_save = True

            # Process content translation
            if target_field == "content" and feed.translate_content:
                youtube_content = uses_youtube_transcript(entry, feed)
                if feed.fetch_article and not youtube_content:
                    article_content = _fetch_article_content(entry.link)
                    if article_content and article_content != entry.original_content:
                        entry.original_content = article_content
                        entry_needs_save = True
                    # Clean up article content after use
                    del article_content

                if (entry.original_content or youtube_content) and not entry.translated_content:
                    metrics = _translate_entry_content(
                        entry=entry,
                        target_language=feed.target_language,
                        engine=feed.translator,
                        user_prompt=feed.additional_prompt,
                        content=processing_content(entry, feed),
                    )
                    entry_tokens += metrics["tokens"]
                    entry_characters += metrics["characters"]
                    entry_needs_save = True

            if entry_needs_save:
                fields = [f"translated_{target_field}"]
                if target_field == "content" and feed.fetch_article and not youtube_content:
                    fields.append("original_content")
                with transaction.atomic():
                    entry.save(update_fields=fields)
                    if entry_tokens or entry_characters:
                        Feed.objects.filter(pk=feed.pk).update(
                            total_tokens=F("total_tokens") + entry_tokens,
                            total_characters=F("total_characters") + entry_characters,
                        )
                feed.total_tokens += entry_tokens
                feed.total_characters += entry_characters
                total_tokens += entry_tokens
                total_characters += entry_characters
        except Exception as e:
            logger.error(f"Error processing entry {entry.link}: {str(e)}")
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


def _translate_entry_title(
    entry: Entry,
    target_language: str,
    engine: Agent,
    user_prompt: str = None,
) -> dict:
    """Translate entry title with memory optimization."""
    total_tokens = 0
    total_characters = 0

    # Check if title needs translation
    if entry.translated_title or not (entry.original_title or "").strip():
        return {"tokens": 0, "characters": 0}

    logger.debug("[Title] Translating title")
    result = auto_retry(
        engine.translate,
        max_retries=3,
        require_text=True,
        text=entry.original_title,
        target_language=target_language,
        text_type="title",
        user_prompt=user_prompt,
    )

    if result:
        translated_title = result.get("text")
        entry.translated_title = translated_title if translated_title else None
        total_tokens = result.get("tokens", 0)
        total_characters = result.get("characters", 0)

    return {"tokens": total_tokens, "characters": total_characters}


def _translate_entry_content(
    entry: Entry,
    target_language: str,
    engine: Agent,
    user_prompt: str = None,
    content: str = None,
) -> dict:
    """Translate entry content with memory optimization."""
    total_tokens = 0
    total_characters = 0

    # Check if content needs translation
    if entry.translated_content:
        return {"tokens": 0, "characters": 0}

    # Parse HTML with explicit cleanup
    soup = BeautifulSoup(entry.original_content if content is None else content, "lxml")

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

    # Perform translation
    result = auto_retry(
        func=engine.translate,
        max_retries=3,
        require_text=True,
        text=processed_html,
        target_language=target_language,
        text_type="content",
        user_prompt=user_prompt,
    )

    translated_content = result.get("text")
    logger.info(f"Translated content: {translated_content[:100]}")
    entry.translated_content = translated_content if translated_content else None
    total_tokens = result.get("tokens", 0)
    total_characters = result.get("characters", 0)

    # Clean up large strings
    del processed_html
    if translated_content:
        del translated_content

    return {"tokens": total_tokens, "characters": total_characters}


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

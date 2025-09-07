import logging
from django.utils import timezone
from bs4 import BeautifulSoup
import mistune
import newspaper

from core.models import Feed, Entry, Agent
from utils import text_handler
from core.tasks.utils import auto_retry

logger = logging.getLogger(__name__)


def handle_feeds_translation(feeds: list, target_field: str = "title"):
    for feed in feeds:
        try:
            if not feed.entries.exists():
                continue

            feed.translation_status = None
            feed.save()
            logger.info(
                "Start translate %s of feed %s to %s",
                target_field,
                feed.feed_url,
                feed.target_language,
            )

            translate_feed(feed, target_field=target_field)
            # feed.translation_status = True
            feed.last_translate = timezone.now()
            feed.log += f"{timezone.now()} Translate Completed <br>"
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
            "total_tokens",
            "total_characters",
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
    entries_to_save = []
    BATCH_SIZE = 30  # Reduced batch size for memory efficiency

    # 只处理前 feed.max_posts 条 entries
    entries = feed.entries.order_by("-pubdate")[: feed.max_posts].iterator(
        chunk_size=50
    )

    for entry in entries:
        translation_status = None
        try:
            logger.debug(f"Processing entry {entry}")
            if not feed.translator:
                raise Exception("Translate Engine Not Set")

            entry_needs_save = False

            # Process title translation
            if target_field == "title" and feed.translate_title:
                metrics = _translate_entry_title(
                    entry=entry,
                    target_language=feed.target_language,
                    engine=feed.translator,
                )
                total_tokens += metrics["tokens"]
                total_characters += metrics["characters"]
                entry_needs_save = True

            # Process content translation
            if (
                target_field == "content"
                and feed.translate_content
                and entry.original_content
            ):
                if feed.fetch_article:
                    article_content = _fetch_article_content(entry.link)
                    if article_content:
                        entry.original_content = article_content
                        entry_needs_save = True
                        # Clean up article content after use
                        del article_content

                metrics = _translate_entry_content(
                    entry=entry,
                    target_language=feed.target_language,
                    engine=feed.translator,
                )
                total_tokens += metrics["tokens"]
                total_characters += metrics["characters"]
                entry_needs_save = True

            if entry_needs_save:
                entries_to_save.append(entry)

            # Batch update with smaller size
            if len(entries_to_save) >= BATCH_SIZE:
                Entry.objects.bulk_update(
                    entries_to_save,
                    fields=[
                        "translated_title",
                        "translated_content",
                        "original_content",
                    ],
                )
                entries_to_save = []
                # Force garbage collection
                import gc

                gc.collect()
            translation_status = True
        except Exception as e:
            logger.error(f"Error processing entry {entry.link}: {str(e)}")
            feed.log += (
                f"{timezone.now()} Error processing entry {entry.link}: {str(e)}<br>"
            )
            translation_status = False
            # feed的翻译状态不应该因单个entry的翻译失败而失败，所以只记录错误日志
        finally:
            feed.translation_status = translation_status
            # Explicitly clean up entry reference
            del entry

    # Save remaining entries
    if entries_to_save:
        Entry.objects.bulk_update(
            entries_to_save,
            fields=["translated_title", "translated_content", "original_content"],
        )
        del entries_to_save

    # Update feed stats
    feed.total_tokens += total_tokens
    feed.total_characters += total_characters

    logger.info(
        f"Translation completed. Tokens: {total_tokens}, Chars: {total_characters}"
    )
    # Clean up large variables
    del entries, total_tokens, total_characters


def _translate_entry_title(
    entry: Entry,
    target_language: str,
    engine: Agent,
) -> dict:
    """Translate entry title with memory optimization."""
    total_tokens = 0
    total_characters = 0

    # Check if title needs translation
    if entry.translated_title:
        return {"tokens": 0, "characters": 0}

    logger.debug("[Title] Translating title")
    result = auto_retry(
        engine.translate,
        max_retries=3,
        text=entry.original_title,
        target_language=target_language,
        text_type="title",
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

    # Perform translation
    result = auto_retry(
        func=engine.translate,
        max_retries=3,
        text=processed_html,
        target_language=target_language,
        text_type="content",
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


def _fetch_article_content(link: str) -> str:
    """Fetch full article content with explicit cleanup."""
    content = ""
    try:
        article = newspaper.Article(link)
        article.download()
        article.parse()
        content = mistune.html(article.text)
    except Exception as e:
        logger.error(f"Article fetch failed: {str(e)}")
    finally:
        # Explicitly clean up newspaper objects
        if "article" in locals():
            del article
        return content

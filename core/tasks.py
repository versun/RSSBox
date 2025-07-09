import logging
import time
from django.utils import timezone
from bs4 import BeautifulSoup
import mistune
import newspaper
from typing import Optional, Tuple
from .models import Feed, Entry
from utils.feed_action import fetch_feed, convert_struct_time_to_datetime
from utils import text_handler
from translator.models import TranslatorEngine


def handle_single_feed_fetch(feed: Feed):
    """
    Fetch feeds and update entries.
    """
    try:
        feed.fetch_status = None
        fetch_results = fetch_feed(url=feed.feed_url, etag=feed.etag)

        if fetch_results["error"]:
            raise Exception(f"Fetch Feed Failed: {fetch_results['error']}")
        elif not fetch_results["update"]:
            raise Exception("Feed is up to date, Skip")

        latest_feed = fetch_results.get("feed")
        # Update feed meta
        feed.name = latest_feed.feed.get("title", "Empty")
        feed.subtitle = latest_feed.feed.get("subtitle")
        feed.language = latest_feed.feed.get("language")
        feed.author = latest_feed.feed.get("author") or "Unknown"
        feed.link = latest_feed.feed.get("link") or feed.feed_url
        feed.pubdate = convert_struct_time_to_datetime(
            latest_feed.feed.get("published_parsed")
        )
        feed.updated = convert_struct_time_to_datetime(
            latest_feed.feed.get("updated_parsed")
        )
        feed.last_fetch = timezone.now()
        feed.etag = latest_feed.get("etag")

        # Update entries
        if getattr(latest_feed, "entries", None):
            entries_to_create = []
            # entries_to_update = []
            # Use values_list for better memory efficiency
            existing_entries = dict(
                Entry.objects.filter(feed=feed).values_list("guid", "id")
            )

            for entry_data in latest_feed.entries[: feed.max_posts]:
                # 获取内容
                content = ""
                if "content" in entry_data:
                    content = entry_data.content[0].value if entry_data.content else ""
                else:
                    content = entry_data.get("summary")

                guid = entry_data.get("id") or entry_data.get("link")
                link = entry_data.get("link", "")
                author = entry_data.get("author", feed.author)
                if not guid:
                    continue  # 跳过无效条目

                # 判断是否需要创建新条目
                if guid not in existing_entries:
                    entry_values = {
                        "link": link,
                        "author": author,
                        "pubdate": convert_struct_time_to_datetime(
                            entry_data.get("published_parsed")
                        ),
                        "updated": convert_struct_time_to_datetime(
                            entry_data.get("updated_parsed")
                        ),
                        "original_title": entry_data.get("title", "No title"),
                        "original_content": content,
                        "original_summary": entry_data.get("summary"),
                        "enclosures_xml": entry_data.get("enclosures_xml"),
                    }

                    entries_to_create.append(
                        Entry(feed=feed, guid=guid, **entry_values)
                    )

                # if guid in existing_entries:
                #     # 更新操作
                #     entry = Entry(
                #         id=existing_entries[guid],  # 直接设置主键
                #         feed=feed,
                #         guid=guid,
                #         **entry_values
                #     )
                #     entries_to_update.append(entry)
                # else:
                #     # 创建操作
                #     entries_to_create.append(Entry(
                #         feed=feed,
                #         guid=guid,
                #         **entry_values
                #     ))

            # 批量执行数据库操作
            if entries_to_create:
                Entry.objects.bulk_create(entries_to_create)
            # if entries_to_update:
            #     update_fields = list(entry_values.keys())
            #     Entry.objects.bulk_update(entries_to_update, fields=update_fields)

        feed.fetch_status = True
        feed.log = f"{timezone.now()} Fetch Completed <br>"
    except Exception as e:
        logging.error("Task handle_single_feed_fetch %s: %s", feed.feed_url, str(e))
        feed.fetch_status = False
        feed.log = f"{timezone.now()} {str(e)}<br>"
    finally:
        feed.save()


def handle_feeds_fetch(feeds: list):
    """
    Fetch feeds and update entries.
    """
    for feed in feeds:
        handle_single_feed_fetch(feed)

    # Feed.objects.bulk_update(
    #         feeds,
    #         fields=[
    #             "fetch_status", "last_fetch", "etag", "log", "name",
    #             "subtitle", "language", "author", "link", "pubdate", "updated"
    #         ]
    #     )


def handle_feeds_translation(feeds: list, target_field: str = "title"):
    for feed in feeds:
        try:
            if not feed.entries.exists():
                continue

            feed.translation_status = None
            logging.info(
                "Start translate %s of feed %s to %s",
                target_field,
                feed.feed_url,
                feed.target_language,
            )

            translate_feed(feed, target_field=target_field)
            feed.translation_status = True
            feed.log += f"{timezone.now()} Translate Completed <br>"
        except Exception as e:
            logging.error(
                "Task handle_feeds_translation (%s)%s: %s",
                feed.target_language,
                feed.feed_url,
                str(e),
            )
            feed.translation_status = False
            feed.log += f"{timezone.now()} {str(e)} <br>"

    Feed.objects.bulk_update(
        feeds, fields=["translation_status", "log", "total_tokens", "total_characters"]
    )


def handle_feeds_summary(feeds: list):
    for feed in feeds:
        try:
            if not feed.entries.exists():
                continue

            feed.translation_status = None
            logging.info(
                "Start summary feed %s to %s", feed.feed_url, feed.target_language
            )

            summarize_feed(feed)
            feed.translation_status = True
            feed.log += f"{timezone.now()} Summary Completed <br>"
        except Exception as e:
            logging.error(
                "Task handle_feeds_summary (%s)%s: %s",
                feed.target_language,
                feed.feed_url,
                str(e),
            )
            feed.translation_status = False
            feed.log += f"{timezone.now()} {str(e)}<br>"

    Feed.objects.bulk_update(
        feeds, fields=["translation_status", "log", "total_tokens", "total_characters"]
    )


def translate_feed(feed: Feed, target_field: str = "title"):
    """Translate and summarize feed entries with enhanced error handling and caching"""
    logging.info(
        "Translating feed: %s (%s items)", feed.target_language, feed.entries.count()
    )
    total_tokens = 0
    total_characters = 0
    entries_to_save = []

    # Use iterator to reduce memory usage for large feeds
    for entry in feed.entries.all().iterator(chunk_size=100):
        try:
            logging.debug(f"Processing entry {entry}")
            if not feed.translator:
                raise Exception("Translate Engine Not Set")

            entry_needs_save = False

            # Process title translation
            if target_field == "title" and feed.translate_title:
                metrics = _translate_title(
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

                metrics = _translate_content(
                    entry=entry,
                    target_language=feed.target_language,
                    engine=feed.translator,
                    quality=feed.quality,
                )
                total_tokens += metrics["tokens"]
                total_characters += metrics["characters"]
                entry_needs_save = True

            if entry_needs_save:
                entries_to_save.append(entry)

            # Batch update to save memory and avoid large transactions
            if len(entries_to_save) >= 50:
                Entry.objects.bulk_update(
                    entries_to_save,
                    fields=["translated_title", "translated_content", "original_content"],
                )
                entries_to_save = []

        except Exception as e:
            logging.error(f"Error processing entry {entry.link}: {str(e)}")
            feed.log += (
                f"{timezone.now()} Error processing entry {entry.link}: {str(e)}<br>"
            )
            continue

    # 批量保存所有修改过的entry
    if entries_to_save:
        Entry.objects.bulk_update(
            entries_to_save,
            fields=["translated_title", "translated_content", "original_content"],
        )

    # 更新feed的统计信息（将在外层批量更新中保存）
    feed.total_tokens += total_tokens
    feed.total_characters += total_characters

    logging.info(
        f"Translation completed. Tokens: {total_tokens}, Chars: {total_characters}"
    )


def _translate_title(
    entry: Entry,
    target_language: str,
    engine: TranslatorEngine,
) -> dict:
    """Translate entry title with caching and retry logic"""
    total_tokens = 0
    total_characters = 0
    # Check if title has been translated
    if entry.translated_title:
        logging.debug(f"[Title] Title already translated: {entry.original_title}")
        return {"tokens": 0, "characters": 0}

    logging.debug("[Title] Translating title")
    result = _auto_retry(
        engine.translate,
        max_retries=3,
        text=entry.original_title,
        target_language=target_language,
        text_type="title",
    )
    if result:
        translated_title = result.get("text")
        entry.translated_title = (
            translated_title if translated_title else entry.original_title
        )
        total_tokens = result.get("tokens", 0)
        total_characters = result.get("characters", 0)
    return {"tokens": total_tokens, "characters": total_characters}


def _translate_content(
    entry: Entry,
    target_language: str,
    engine: TranslatorEngine,
    quality: bool = False,
) -> dict:
    """Translate entry content with optimized caching"""
    total_tokens = 0
    total_characters = 0
    # 检查是否已经翻译过
    if entry.translated_content:
        logging.debug(f"[Content] Content already translated: {entry.original_title}")
        return {"tokens": 0, "characters": 0}

    soup = BeautifulSoup(entry.original_content, "lxml")
    # if quality:
    #     soup = BeautifulSoup(text_handler.unwrap_tags(soup), "lxml")

    # add notranslate class and translate="no" to elements that should not be translated
    for element in soup.find_all(string=True):
        if not element.get_text(strip=True):
            continue
        if text_handler.should_skip(element):
            logging.debug("[Content] Skipping element %s", element.parent)
            # 标记父元素不翻译
            parent = element.parent
            parent.attrs.update(
                {"class": parent.get("class", []) + ["notranslate"], "translate": "no"}
            )

    # TODO 如果文字长度大于最大长度，就分段翻译
    processed_html = str(soup)

    logging.debug(f"[Content] Translating content: {entry.original_title}")
    result = _auto_retry(
        func=engine.translate,
        max_retries=3,
        text=processed_html,
        target_language=target_language,
        text_type="content",
    )
    translated_content = result.get("text")
    entry.translated_content = (
        translated_content if translated_content else processed_html
    )
    total_tokens = result.get("tokens", 0)
    total_characters = result.get("characters", 0)

    return {"tokens": total_tokens, "characters": total_characters}


def summarize_feed(
    feed: Feed,
    min_chunk_size: int = 300,
    max_chunk_size: int = 1500,
    summarize_recursively: bool = True,
    max_context_chunks: int = 4,
    max_context_tokens: int = 3000,
    chunk_delimiter: str = ".",
    max_chunks_per_entry: int = 20
):
    """
    Generate content summary using adaptive chunking and context-aware summarization
    
    Args:
        feed: Feed object containing entries to summarize
        min_chunk_size: Minimum token size per chunk
        max_chunk_size: Maximum token size per chunk
        summarize_recursively: Whether to use recursive summarization
        max_context_chunks: Max number of previous summaries to include as context
        max_context_tokens: Max token count for context window
        chunk_delimiter: Primary delimiter for chunking
        max_chunks_per_entry: Safety limit to prevent excessive chunking
    """
    assert 0 <= feed.summary_detail <= 1, "summary_detail must be between 0 and 1"
    entries_to_save = []
    total_tokens = 0  # Track tokens separately to avoid race conditions
    
    try:
        # Prefetch entries to reduce database queries
        entries = feed.entries.select_related('feed').filter(ai_summary__isnull=True)
        total_entries = entries.count()
        
        if not total_entries:
            logging.info(f"No entries to summarize for feed: {feed.feed_url}")
            return
            
        logging.info(f"Starting summary for {total_entries} entries in feed: {feed.feed_url}")
        
        for idx, entry in enumerate(entries.iterator(chunk_size=50)):
            try:
                logging.info(f"[{idx+1}/{total_entries}] Processing: {entry.original_title}")
                
                # Clean and prepare content
                content_text = text_handler.clean_content(entry.original_content)
                
                # Skip empty content
                if not content_text.strip():
                    logging.warning(f"Empty content: {entry.original_title}")
                    entry.ai_summary = "[No content available]"
                    entries_to_save.append(entry)
                    continue
                
                # Calculate target chunk count based on summary detail
                token_count = text_handler.get_token_count(content_text)
                min_chunks = 1
                max_chunks = max(1, min(max_chunks_per_entry, token_count // min_chunk_size))
                target_chunks = max(1, min(max_chunks, int(
                    min_chunks + feed.summary_detail * (max_chunks - min_chunks)
                )))
                
                # Generate adaptive chunks
                text_chunks = text_handler.adaptive_chunking(
                    content_text,
                    target_chunks=target_chunks,
                    min_chunk_size=min_chunk_size,
                    max_chunk_size=max_chunk_size,
                    initial_delimiter=chunk_delimiter
                )
                
                actual_chunks = len(text_chunks)
                logging.info(f"Chunked into {actual_chunks} chunks (target: {target_chunks})")
                
                # Handle small content directly
                if actual_chunks == 1:
                    response = _auto_retry(
                        feed.summarizer.summarize,
                        max_retries=3,
                        text=text_chunks[0],
                        target_language=feed.target_language,
                        #max_tokens=max_context_tokens
                    )
                    entry.ai_summary = response.get("text", "")
                    total_tokens += response.get("tokens", 0)
                    entries_to_save.append(entry)
                    continue
                
                # Process chunks with context management
                accumulated_summaries = []
                context_token_count = 0
                
                for chunk_idx, chunk in enumerate(text_chunks):
                    # Prepare context for recursive summarization
                    context_parts = []
                    if summarize_recursively and accumulated_summaries:
                        # Select recent summaries within context limits
                        context_candidates = accumulated_summaries[-max_context_chunks:]
                        
                        # Build context within token limits
                        for summary in reversed(context_candidates):
                            summary_tokens = text_handler.get_token_count(summary)
                            if context_token_count + summary_tokens <= max_context_tokens:
                                context_parts.insert(0, summary)
                                context_token_count += summary_tokens
                            else:
                                break
                    
                    # Construct prompt with context
                    if context_parts:
                        context_str = "\n\n".join(context_parts)
                        prompt = (
                            f"Previous context summaries:\n\n{context_str}\n\n"
                            f"Current text to summarize:\n\n{chunk}"
                        )
                    else:
                        prompt = chunk
                    
                    # Summarize with retry and token limit
                    response = _auto_retry(
                        feed.summarizer.summarize,
                        max_retries=3,
                        text=prompt,
                        target_language=feed.target_language,
                        max_tokens=max_context_tokens
                    )
                    
                    chunk_summary = response.get("text", "")
                    accumulated_summaries.append(chunk_summary)
                    total_tokens += response.get("tokens", 0)
                    context_token_count = text_handler.get_token_count(chunk_summary)
                    
                    # Progress logging
                    if (chunk_idx + 1) % 5 == 0 or (chunk_idx + 1) == actual_chunks:
                        progress = f"Chunk {chunk_idx+1}/{actual_chunks}"
                        logging.info(f"{progress} - Summary tokens: {response.get('tokens', 0)}")
                
                # Finalize and store summary
                entry.ai_summary = "\n\n".join(accumulated_summaries)
                entries_to_save.append(entry)
                logging.info(f"Completed summary for '{entry.original_title}' - Total tokens: {total_tokens}")
                
                # Periodically save progress
                if (idx + 1) % 10 == 0:
                    _save_progress(entries_to_save, feed, total_tokens)
                    entries_to_save = []
                    total_tokens = 0
                    
            except Exception as e:
                logging.error(f"Error processing entry '{entry.original_title}': {str(e)}")
                entry.ai_summary = f"[Summary failed: {str(e)}]"
                entries_to_save.append(entry)
    
    except Exception as e:
        logging.exception(f"Critical error summarizing feed {feed.feed_url}")
        feed.log += f"{timezone.now()} Critical error: {str(e)}<br>"
    finally:
        _save_progress(entries_to_save, feed, total_tokens)
        logging.info(f"Completed summary process for feed: {feed.feed_url}")

def _save_progress(entries_to_save, feed, total_tokens):
    """Save progress and update token count atomically"""
    if entries_to_save:
        Entry.objects.bulk_update(entries_to_save, fields=["ai_summary"])
    
    if total_tokens > 0:
        feed.total_tokens += total_tokens
        


def _auto_retry(func: callable, max_retries: int = 3, **kwargs) -> dict:
    """Retry translation function with exponential backoff"""
    for attempt in range(max_retries):
        try:
            return func(**kwargs)
        except Exception as e:
            logging.error(f"Translation attempt {attempt + 1} failed: {str(e)}")
        time.sleep(0.5 * (2**attempt))  # Exponential backoff
    logging.error(f"All {max_retries} attempts failed for translation")
    return {}


def _fetch_article_content(link: str) -> str:
    """Fetch full article content using newspaper"""
    try:
        article = newspaper.article(link)
        return mistune.html(article.text)
    except Exception as e:
        logging.error(f"Article fetch failed: {str(e)}")
    return ""

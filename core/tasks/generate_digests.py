import logging
from django.utils import timezone

from core.models.digest import Digest
from core.models.entry import Entry
from utils.text_handler import clean_content, get_token_count
from config import settings

logger = logging.getLogger(__name__)


class DigestGenerator:
    """
    AI-powered digest content generator.

    Processes articles from specified tags and generates comprehensive
    daily/weekly briefings using OpenAI agents.
    """

    def __init__(self, digest: Digest):
        self.digest = digest
        self.articles = []
        # self.temp_translations = temp_translations or {}

    def prepare_articles(self):
        """
        Fetch and prepare articles for digest generation.

        Returns:
            list: Processed article data
        """
        logger.info(f"Preparing articles for digest '{self.digest.name}'")
        # Get articles based on digest configuration
        articles = self.digest.get_articles_for_digest()

        processed_articles = []
        for entry in articles:
            summary = entry.ai_summary or ""

            # Use temporary translation if available, otherwise fallback to Entry translation
            title = entry.original_title or entry.translated_title or "No title"

            processed_articles.append(
                {
                    "title": title,
                    "link": entry.link,
                    "summary": summary,
                    "published": entry.pubdate,
                    "author": entry.author or "Unknown",
                }
            )

        self.articles = processed_articles

        return processed_articles

    def build_prompt(self):
        """
        Build AI prompt with article data.

        Returns:
            tuple: (articles_list, system_prompt, url_mapping) - 分离的文章内容、系统提示和URL映射
        """
        logger.info(f"Building prompt for digest '{self.digest.name}'")
        # Prepare articles text with URL optimization
        articles_list = []
        url_mapping = {}  # 存储占位符到真实URL的映射

        for i, article in enumerate(self.articles, 1):
            # 使用markdown格式的占位符，让AI更容易保留
            url_placeholder = f"LINK_{i}"
            url_mapping[url_placeholder] = article["link"]

            articles_list.append(f"""
Original Title: {article["title"]}
Link: {url_placeholder}
Published: {article["published"]}
Summary: {article["summary"]}
""")

        # Replace placeholders in system prompt using simple string replacement
        output_format_for_digest_prompt = (
            settings.output_format_for_digest_prompt.replace(
                "{digest_name}", self.digest.name
            )
            .replace("{date}", timezone.now().strftime("%Y-%m-%d"))
            .replace("{target_language}", self.digest.target_language)
            .replace("{description}",self.digest.description)
        )
        system_prompt = self.digest.prompt + output_format_for_digest_prompt

        # 计算token消耗，如果超出限制，则进行分块
        safe_tokens = self.digest.summarizer.max_tokens * 0.65
        available_tokens = safe_tokens - get_token_count(system_prompt)  # 预留给输出

        articles: list[str] = self._chunk_articles_by_token_limit(
            articles_list, available_tokens
        )
        return articles, system_prompt, url_mapping

    def generate(self, force: bool = False):
        """
        Generate digest content using AI.

        Returns:
            dict: Generation result with success status and content
        """
        logger.info(f"Generating digest '{self.digest.name}'")
        try:
            # Pre-steps previously handled in generate_digest()
            # 1) Create temporary translations for digest-specific language
            #    to ensure consistent language in digest output without
            #    polluting Entry translations
            # self.temp_translations = _ensure_entries_have_translated_titles(self.digest) or {}

            # 2) Ensure all entries have AI summaries before generating digest
            _ensure_entries_have_summaries(self.digest)

            self.prepare_articles()

            if not self.articles:
                raise Exception("No articles were found within the specified range.") #在设定的时间范围内没有找到文章

            # Build prompt - 分离文章内容、系统提示和URL映射
            articles_list, system_prompt, url_mapping = self.build_prompt()

            # Call AI agent
            logger.info(f"Calling AI agent for digest '{self.digest.name}'")
            logger.info(f"Total articles to digest: {len(articles_list)}")
            now = timezone.now()
            digests_list = []
            final_digest = ""
            for articles_text in articles_list:
                logger.info(f"Digesting article")
                result = self.digest.summarizer.digester(
                    text=articles_text,  # 合并后的多篇文章内容
                    system_prompt=system_prompt,  # 处理指令
                    digest_name=self.digest.name,
                    date=now.strftime("%Y-%m-%d"),
                )

                if result.get("text"):
                    logger.info(f"Digested article")
                    # 补充URL：将占位符替换回真实URL
                    final_content = result["text"]
                    # final_digest += "\n" + final_content
                    digests_list.append(final_content)
                else:
                    logger.warning(f"Failed to digest article")
                self.digest.total_tokens += result.get("tokens", 0)

            # Only for test
            digests_list += digests_list
            if len(digests_list) > 1:
                logger.info(
                    f"Final digest has {len(digests_list)} digests, need to merge to one digest"
                )
                # TODO: 是否还需要再次调用summarizer来合并总结 或者 直接合并digests_list
                result = self.digest.summarizer.digester(
                    text="\n".join(digests_list),
                    system_prompt=system_prompt,
                    digest_name=self.digest.name,
                    date=now.strftime("%Y-%m-%d"),
                )
                if result.get("text"):
                    logger.info(f"Merged digest")
                    final_digest = result["text"]
                else:
                    logger.warning(f"Failed to merge digest")
            else:
                final_digest = digests_list[0]

            for placeholder, real_url in url_mapping.items():
                placeholder = f"({placeholder})"
                real_url = f"({real_url})"
                if placeholder in final_digest:
                    final_digest = final_digest.replace(placeholder, real_url)
                    logger.info(f"Replaced placeholder {placeholder} with URL")

            # 将生成内容保存为一个 Entry，写入 ai_summary
            self.digest.last_generated = now

            # 获取/创建 Digest 专用 Feed
            digest_feed = self.digest.get_digest_feed()

            # 创建一条新的摘要 Entry
            entry = Entry.objects.create(
                feed=digest_feed,
                link=f"{settings.SITE_URL.rstrip('/')}/core/digest/{self.digest.slug}",
                author=self.digest.name or "Digest",
                pubdate=now,
                updated=now,
                guid=f"digest:{self.digest.id}:{int(now.timestamp())}",
                original_title=f"{self.digest.name} | {now.strftime('%Y-%m-%d %H:%M')}",
                translated_title=None,
                original_content=None,
                translated_content=None,
                original_summary=None,
                ai_summary=final_digest,
            )

            self.digest.status = True  # Set status to success

            return {
                "success": True,
                "entry_id": entry.id,
            }

        except Exception as e:
            logger.error(f"Digest generation failed for {self.digest.name}: {e}")
            self.digest.log += f"Digest generation failed for {self.digest.name}: {e}\n"
            # Mark status as failed
            self.digest.status = False
            return {"success": False, "error": str(e)}
        finally:
            self.digest.save()

    def _chunk_articles_by_token_limit(
        self, articles_list: list[str], max_tokens
    ) -> list[str]:
        """
        Split articles text into chunks.
        Returns [articles_text, articles_text, ...] where each articles_text is <= max_tokens
        """
        logger.info(f"Chunking articles for digest '{self.digest.name}'")
        # 都是 AI summary，通常不会很长；按 token 限制合并为若干块
        chunks: list[str] = []
        current_chunk: str = ""
        current_tokens: int = 0
        max_tokens = int(max_tokens) if max_tokens else 0
        logger.info(
            f"Total articles to chunk: {len(articles_list)}; token limit per chunk: {max_tokens}"
        )
        for articles_text in articles_list:
            article_tokens = get_token_count(articles_text)
            if (
                current_chunk
                and current_tokens + article_tokens > max_tokens
                and max_tokens > 0
            ):
                # 关闭当前块，开启新块
                chunks.append(current_chunk)
                current_chunk = articles_text
                current_tokens = article_tokens
                logger.debug("Started a new chunk due to token limit")
            else:
                # 追加到当前块
                if current_chunk:
                    current_chunk += "\n" + articles_text
                    current_tokens += article_tokens
                else:
                    current_chunk = articles_text
                    current_tokens = article_tokens
        # 收尾，把最后的块加入
        if current_chunk:
            chunks.append(current_chunk)
        logger.info(f"Total chunks produced: {len(chunks)}")
        return chunks


def _ensure_entries_have_translated_titles(digest: Digest):
    """
    Generate temporary translations for digest entries without caching to Entry model.

    This ensures consistent language in digest output by creating temporary translations
    to the digest's target language. Translations are NOT saved to Entry.translated_title
    to avoid polluting Feed-specific translations.

    Only processes articles within the digest's days_range to avoid unnecessary work.

    Args:
        digest: The Digest instance

    Returns:
        dict: Mapping of entry_id to translated_title for digest use
    """
    from core.models.feed import Feed
    from core.tasks.utils import auto_retry

    # Get articles for digest within the specified days_range
    all_articles = list(digest.get_articles_for_digest())

    if not all_articles:
        logger.info(f"No articles found for digest '{digest.name}'")
        return {}

    logger.info(
        f"Processing {len(all_articles)} entries for temporary title translation to {digest.target_language}..."
    )

    # Create temporary translation cache for this digest
    temp_translations = {}

    # Group entries by feed to process efficiently
    feed_ids = set(entry.feed_id for entry in all_articles)
    candidate_feeds = Feed.objects.filter(id__in=feed_ids)

    if not candidate_feeds.exists():
        logger.warning(f"Found {len(all_articles)} entries but their feeds don't exist")
        return

    # Process temporary translation for each entry
    digest_tokens = 0  # Tokens for digest-specific translations
    total_tokens = 0  # Tokens for feed translations
    total_characters = 0
    translated_count = 0
    feeds_to_update = {}  # Track feed token usage
    use_digest_summarizer = False

    for entry in all_articles:
        try:
            # Find the feed for this entry
            feed = next((f for f in candidate_feeds if f.id == entry.feed_id), None)
            if not feed:
                continue

            # First priority: use existing translated_title if available
            if entry.translated_title and entry.translated_title.strip():
                temp_translations[entry.id] = entry.translated_title
                continue

            # No existing translation - determine translator
            if not feed.translator:
                use_digest_summarizer = True
                translator = digest.summarizer
            else:
                translator = feed.translator

            # Determine what title to use based on language match
            if feed.target_language == digest.target_language:
                # Same language - trigger Feed translation to get translated_title
                from core.tasks.translate_feeds import _translate_entry_title

                metrics = _translate_entry_title(
                    entry=entry,
                    target_language=feed.target_language,
                    engine=translator,
                )

                total_tokens += metrics["tokens"]
                total_characters += metrics["characters"]

                # Save the translation to Entry for future use
                if metrics["tokens"] > 0:
                    entry.save(update_fields=["translated_title"])
                    translated_count += 1

                temp_translations[entry.id] = (
                    entry.translated_title or entry.original_title
                )

            else:
                if not entry.original_title:
                    # Fallback to original title if no translator or no content
                    # temp_translations[entry.id] = entry.original_title
                    continue

                # Perform temporary translation (not saved to Entry)
                logger.debug(
                    f"[Digest Temp Translation] Translating title for entry {entry.id}"
                )
                result = auto_retry(
                    translator.translate,
                    max_retries=3,
                    text=entry.original_title,
                    target_language=digest.target_language,
                    text_type="title",
                )

                if result and result.get("text"):
                    temp_translations[entry.id] = result.get("text")
                    digest_tokens += result.get("tokens", 0)
                    total_characters += result.get("characters", 0)
                    translated_count += 1
                else:
                    # Fallback to original title
                    temp_translations[entry.id] = entry.original_title
                # Update digest token counts if we used digest's summarizer
            if use_digest_summarizer:
                digest.total_tokens += total_tokens
                digest.save()
            else:
                feed.total_tokens += total_tokens
                feed.total_characters += total_characters
                feed.save()

        except Exception as e:
            logger.error(
                f"Error creating temporary translation for entry {entry.id}: {e}"
            )
            digest.log += (
                f"Error creating temporary translation for entry {entry.id}: {e}\n"
            )
            digest.status = False
            digest.save()
            # Fallback to original title
            temp_translations[entry.id] = entry.original_title or "No title"
    return temp_translations


def _ensure_entries_have_summaries(digest: Digest):
    """
    Ensure all entries that will be included in the digest have AI summaries.

    This is a critical dependency - digest quality depends on having proper AI summaries
    for all entries. Without this, the digest would use fallback content which is
    much lower quality.

    Only processes articles within the digest's days_range to avoid unnecessary work.

    Args:
        digest: The Digest instance
    """
    from core.tasks.summarize_feeds import _summarize_entry
    from core.models.entry import Entry
    import gc

    # Get articles for digest within the specified days_range
    all_articles = list(digest.get_articles_for_digest())

    if not all_articles:
        logger.info(f"No articles found for digest '{digest.name}'")
        return

    # Filter entries that need summaries - prioritize existing ai_summary
    entries_without_summary = [
        entry
        for entry in all_articles
        if not entry.ai_summary or entry.ai_summary.strip() == ""
    ]

    if not entries_without_summary:
        logger.info(f"All entries for digest '{digest.name}' already have AI summaries")
        return

    logger.info(
        f"Found {len(entries_without_summary)} entries without AI summaries. Generating summaries..."
    )

    # Process each entry directly - no need to group by feed
    entries_to_save = []
    total_tokens = 0
    BATCH_SIZE = 5  # Memory-efficient batch size
    use_digest_summarizer = False

    for idx, entry in enumerate(entries_without_summary):
        try:
            # Determine which summarizer to use
            if entry.feed.summarizer:
                summarizer = entry.feed.summarizer
                target_language = entry.feed.target_language
                summary_detail = entry.feed.summary_detail or 0.0
            elif digest.summarizer:
                # Use digest's summarizer as fallback
                summarizer = digest.summarizer
                target_language = digest.target_language
                summary_detail = 0.0  # Default detail level for digest fallback
                use_digest_summarizer = True
                logger.info(
                    f"Using digest summarizer for entry '{entry.original_title}' "
                    f"from feed '{entry.feed.name}' that doesn't have its own summarizer"
                )
            else:
                logger.warning(
                    f"Entry '{entry.original_title}' from feed '{entry.feed.name}' "
                    f"has no summarizer and digest has no fallback summarizer"
                )
                continue

            logger.info(
                f"[{idx + 1}/{len(entries_without_summary)}] Processing: {entry.original_title}"
            )

            # Generate summary for this entry directly
            summary, entry_tokens = _summarize_entry(
                entry=entry,
                summarizer=summarizer,
                target_language=target_language,
                min_chunk_size=summarizer.min_size(),
                max_chunk_size=summarizer.max_size(),
                summarize_recursively=True,
                max_context_chunks=4,
                max_context_tokens=summarizer.max_tokens,
                chunk_delimiter=".",
                max_chunks_per_entry=20,
                summary_detail=summary_detail,
            )

            entry.ai_summary = summary
            total_tokens += entry_tokens
            entries_to_save.append(entry)

            logger.info(
                f"Completed summary for '{entry.original_title}' - Tokens: {entry_tokens}"
            )

            # Periodically save progress with smaller batch size
            if len(entries_to_save) >= BATCH_SIZE:
                _save_progress_batch(
                    entries_to_save, digest, total_tokens, use_digest_summarizer
                )
                total_tokens = 0
                entries_to_save = []

                # Force garbage collection
                gc.collect()

        except Exception as e:
            logger.error(
                f"Error generating summary for entry '{entry.original_title}': {e}"
            )
            digest.log += (
                f"Error generating summary for entry '{entry.original_title}': {e}\n"
            )
            digest.status = False
            digest.save()
            entry.ai_summary = f"[Summary failed: {str(e)}]"
            entries_to_save.append(entry)

    if entries_to_save:
        _save_progress_batch(
            entries_to_save, digest, total_tokens, use_digest_summarizer
        )


def _save_progress_batch(entries_to_save, digest, total_tokens, use_digest_summarizer):
    """Save progress with memory cleanup."""
    if entries_to_save:
        from core.models.entry import Entry

        Entry.objects.bulk_update(entries_to_save, fields=["ai_summary"])
        del entries_to_save

    if total_tokens > 0 and use_digest_summarizer:
        digest.total_tokens += total_tokens
        digest.save()

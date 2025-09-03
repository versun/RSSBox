import logging
from datetime import datetime, timedelta
from concurrent.futures import TimeoutError
from django.utils import timezone

from core.models.digest import Digest
from core.models.entry import Entry
from core.models.feed import Feed
from utils.text_handler import clean_content, get_token_count
from config import settings

logger = logging.getLogger(__name__)


class DigestGenerator:
    """
    AI-powered digest content generator.
    
    Processes articles from specified tags and generates comprehensive
    daily/weekly briefings using OpenAI agents.
    """
    
    def __init__(self, digest: Digest, task_manager=None, task_name=None):
        self.digest = digest
        self.articles = []
        self.task_manager = task_manager
        self.task_name = task_name
        
    def prepare_articles(self):
        """
        Fetch and prepare articles for digest generation.
        
        Returns:
            list: Processed article data
        """
        # Get articles based on digest configuration
        articles = self.digest.get_articles_for_digest()
        
        processed_articles = []
        for entry in articles:
            # Now we can be more confident that ai_summary exists
            # because _ensure_entries_have_summaries was called before at digest_generation in tasks.py
            content = (
                entry.ai_summary or 
                entry.original_summary or 
                clean_content(entry.original_content or "")[:300]
            )
            
            processed_articles.append({
                "title": entry.original_title or entry.translated_title or "No title",
                "link": entry.link,
                "content": content,
                "published": entry.pubdate,
                "author": entry.author or "Unknown",
            })
        
        # 根据token限制预处理文章数量，避免分块问题
        if self.digest.summarizer and hasattr(self.digest.summarizer, 'max_tokens'):
            max_tokens = self.digest.summarizer.max_tokens
            if max_tokens > 0:
                # 动态计算每篇文章的实际token消耗
                if processed_articles:
                    # 计算前几篇文章的平均token消耗作为样本
                    sample_size = min(3, len(processed_articles))
                    sample_tokens = 0
                    for article in processed_articles[:sample_size]:
                        article_text = f"**{article['title']}** {article['content']}"
                        sample_tokens += get_token_count(article_text)
                    
                    avg_tokens_per_article = max(100, sample_tokens // sample_size)  # 至少100 token
                else:
                    avg_tokens_per_article = 150  # 默认估计
                
                # 系统提示和模板的token消耗（更精确估算）
                template_text = self.digest.prompt or ""
                system_tokens = get_token_count(template_text) + get_token_count(settings.output_format_for_filter_prompt)
                
                # 计算最大可处理文章数量（保留25%缓冲，比之前的30%更激进）
                safe_tokens = max_tokens * 0.75
                available_tokens = safe_tokens - system_tokens - 1000  # 预留1000给输出
                
                max_articles = max(1, int(available_tokens / avg_tokens_per_article))
                
                if len(processed_articles) > max_articles:
                    logger.info(
                        f"Token optimization: {len(processed_articles)} articles → {max_articles} articles "
                        f"(avg: {avg_tokens_per_article} tokens/article, available: {available_tokens} tokens)"
                    )
                    # 优先保留最新的文章
                    processed_articles.sort(key=lambda x: x['published'] or timezone.now(), reverse=True)
                    processed_articles = processed_articles[:max_articles]
        
        self.articles = processed_articles
        
        # 报告进度
        if self.task_manager and self.task_name:
            self.task_manager.update_progress(self.task_name, 25)
        
        return processed_articles
    
    def build_prompt(self):
        """
        Build AI prompt with article data.
        
        Returns:
            tuple: (articles_text, system_prompt) - 分离的文章内容和系统提示
        """
        # Prepare articles text
        articles_text = ""
        for i, article in enumerate(self.articles, 1):
            articles_text += f"""
{i}. **{article['title']}**
URL: {article['link']}
Published: {article['published']}
Content: {article['content']}

"""
        
        # Use template from digest or default
        template_str = self.digest.prompt
        
        # Replace placeholders in system prompt using Python string formatting
        system_prompt = template_str.format(
            digest_name=self.digest.name,
            date=timezone.now().strftime("%Y-%m-%d"),
            target_language=self.digest.target_language,
        )
        logger.info(f"!!!!!!!!!! System prompt: {system_prompt}")
        return articles_text, system_prompt
    
    def generate(self):
        """
        Generate digest content using AI.
        
        Returns:
            dict: Generation result with success status and content
        """
        try:
            # Prepare articles
            self.prepare_articles()
            
            if not self.articles:
                return {
                    'success': False,
                    'error': 'No articles found for digest generation'
                }
            
            # 报告进度
            if self.task_manager and self.task_name:
                self.task_manager.update_progress(self.task_name, 50)
            
            # Build prompt - 分离文章内容和系统提示
            articles_text, system_prompt = self.build_prompt()
            
            # 报告进度
            if self.task_manager and self.task_name:
                self.task_manager.update_progress(self.task_name, 75)
            
            # Call AI agent
            # 正确分离text（文章内容）和system_prompt（处理指令）
            result = self.digest.summarizer.digester(
                text=articles_text,  # 纯文章内容
                target_language=self.digest.target_language,  # 使用 Digest 的目标语言
                system_prompt=system_prompt  # 处理指令
            )
            
            if result.get('text'):
                # 将生成内容保存为一个 Entry，写入 ai_summary
                now = timezone.now()
                self.digest.last_generated = now
                self.digest.status = True  # Set status to success
                self.digest.save()

                # 获取/创建 Digest 专用 Feed
                digest_feed = get_or_create_digest_feed(self.digest)

                # 创建一条新的摘要 Entry
                Entry.objects.create(
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
                    ai_summary=result['text'],
                )
                
                # 报告完成进度
                if self.task_manager and self.task_name:
                    self.task_manager.update_progress(self.task_name, 100)
                
                return {
                    'success': True,
                    'content': result['text'],
                    'articles_processed': len(self.articles)
                }
            else:
                # Mark status as failed
                self.digest.status = False
                self.digest.save()
                
                return {
                    'success': False,
                    'error': 'AI generation failed - no content returned'
                }
                
        except Exception as e:
            logger.error(f"Digest generation failed for {self.digest.name}: {e}")
            
            # Mark status as failed
            self.digest.status = False
            self.digest.save()
            
            return {
                'success': False,
                'error': str(e)
            }


def generate_digest(digest_id: int, force: bool = False, task_manager=None, task_name=None):
    """
    Generate digest content for given digest ID.
    
    Args:
        digest_id: Digest model ID
        force: Force generation even if already generated today
        task_manager: Optional task manager instance for progress reporting
        task_name: Optional task name for progress reporting
        
    Returns:
        dict: Generation result
    """
    try:
        digest = Digest.objects.get(id=digest_id)
        
        # Check if generation needed
        if not force and not digest.should_generate_today():
            return {
                'success': False,
                'error': 'Digest already generated today or is inactive'
            }
        
        # CRITICAL: Ensure all entries have AI summaries before generating digest
        # This is the key dependency - digest quality depends on AI summaries
        _ensure_entries_have_summaries(digest, task_manager, task_name)
        
        # Generate content with progress reporting
        generator = DigestGenerator(digest, task_manager, task_name)
        return generator.generate()
        
    except Digest.DoesNotExist:
        return {
            'success': False,
            'error': f'Digest with ID {digest_id} not found'
        }


def get_or_create_digest_feed(digest: Digest) -> Feed:
    """
    为指定 Digest 获取或创建一个专用的 Feed，用于承载摘要 Entry。
    """
    from config import settings as project_settings

    feed_url = f"{project_settings.SITE_URL.rstrip('/')}/core/digest/rss/{digest.slug}"
    defaults = {
        "name": digest.name,
        "subtitle": f"AI Digest for {digest.name}",
        "link": f"{project_settings.SITE_URL.rstrip('/')}/core/digest/{digest.slug}",
        "author": digest.name or "Digest",
        "language": digest.target_language,
        # 以分钟为单位，默认 1 天
        "update_frequency": 1440,
        # 不进行原文抓取
        "fetch_article": False,
        # 不进行翻译
        "translate_title": False,
        "translate_content": False,
        # 允许摘要信息加入 RSS
        "summary": True,
        "target_language": project_settings.DEFAULT_TARGET_LANGUAGE,
    }

    feed, _ = Feed.objects.get_or_create(
        feed_url=feed_url,
        target_language=defaults["target_language"],
        defaults=defaults,
    )

    return feed


def _ensure_entries_have_summaries(digest: Digest, task_manager, task_name):
    """
    Ensure all entries that will be included in the digest have AI summaries.
    
    This is a critical dependency - digest quality depends on having proper AI summaries
    for all entries. Without this, the digest would use fallback content which is
    much lower quality.
    
    Args:
        digest: The Digest instance
        task_manager: Task manager for progress updates
        task_name: Current task name for progress tracking
    """
    from core.models.feed import Feed
    from core.tasks import handle_feeds_summary
    from core.tasks.task_manager import task_manager as global_task_manager
    import time
    
    # Get all articles for digest first
    all_articles = list(digest.get_articles_for_digest())  # Convert to list to avoid slicing issues
    
    # Filter entries that need summaries
    entries_without_summary = [
        entry for entry in all_articles 
        if entry.ai_summary is None or entry.ai_summary == ""
    ]
    
    if not entries_without_summary:
        logger.info(f"All entries for digest '{digest.name}' already have AI summaries")
        return
    
    # Group entries by feed to process efficiently
    feed_ids = set(entry.feed_id for entry in entries_without_summary)
    
    # Get all feeds that need summary processing
    candidate_feeds = Feed.objects.filter(
        id__in=feed_ids,
    )
    
    if not candidate_feeds.exists():
        logger.warning(
            f"Found {len(entries_without_summary)} entries without summaries, "
            f"but their feeds don't have summary enabled"
        )
        return
    
    # Prepare feeds for summarization with fallback logic
    feeds_to_summarize = []
    feeds_without_summarizer = []
    
    for feed in candidate_feeds:
        # Check if feed has its own summarizer
        if feed.summarizer:
            # Feed has its own summarizer
            feeds_to_summarize.append(feed)
        else:
            # Feed doesn't have summarizer, will use digest's summarizer as fallback
            feeds_without_summarizer.append(feed)
    
    # For feeds without summarizer, temporarily assign digest's summarizer
    if feeds_without_summarizer and digest.summarizer:
        logger.info(
            f"Assigning digest summarizer to {len(feeds_without_summarizer)} feeds "
            f"that don't have their own summarizer configured"
        )
        
        for feed in feeds_without_summarizer:
            # Temporarily assign digest's summarizer
            feed.summarizer = digest.summarizer
            feeds_to_summarize.append(feed)
    
    if not feeds_to_summarize:
        logger.warning(
            f"Found {len(entries_without_summary)} entries without summaries, "
            f"but no valid summarizer available (neither feed nor digest has summarizer configured)"
        )
        return
    
    logger.info(
        f"Found {len(entries_without_summary)} entries without AI summaries "
        f"across {len(feeds_to_summarize)} feeds. Generating summaries..."
    )
    
    # Update progress
    if task_manager and task_name:
        task_manager.update_progress(task_name, 10)
    
    # Submit summary generation task
    summary_task_name = f"digest_{digest.id}_summaries_{int(time.time())}"
    
    try:
        # Use the global task manager to submit the task
        tm = task_manager or global_task_manager
        future = tm.submit_task(
            summary_task_name,
            handle_feeds_summary,
            feeds_to_summarize  # Already a list
        )
        
        # Wait for summaries to complete with timeout
        # This is synchronous by design - digest quality depends on having summaries
        logger.info(f"Waiting for summary generation task: {summary_task_name}")
        
        timeout = 300  # 5 minutes timeout
        try:
            # Use Future.result() with timeout - much cleaner than polling
            result = future.result(timeout=timeout)
            logger.info("Summary generation completed successfully")
            
            # Update progress after completion
            if task_manager and task_name:
                task_manager.update_progress(task_name, 20)
                
        except TimeoutError:
            logger.error(f"Summary generation timed out after {timeout} seconds")
            # Continue anyway - digest will use fallback content
        except Exception as e:
            logger.error(f"Summary generation task failed: {e}")
            # Continue anyway - digest will use fallback content
        
    except Exception as e:
        logger.error(f"Failed to generate summaries for digest: {e}")
        # Continue with digest generation - it will use fallback content
    
    # Final progress update
    if task_manager and task_name:
        task_manager.update_progress(task_name, 20)
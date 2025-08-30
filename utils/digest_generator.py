"""
日报文章生成器
基于聚类结果和AI代理生成深度分析文章
"""

import json
import logging
import re
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

from django.utils import timezone
from core.models.entry import Entry
from core.models.digest import Digest, DigestArticle
from utils.clustering_service import ClusterSuggestion

logger = logging.getLogger(__name__)


class DigestArticleGenerator:
    """日报文章生成器"""
    
    def __init__(self, ai_agent=None, target_language: str = "中文"):
        self.ai_agent = ai_agent
        self.target_language = target_language
        
        # 默认文章生成提示词模板
        self.default_article_prompt = """
You are a professional news analyst tasked with generating comprehensive analysis articles based on related news entries.

Article Requirements:
1. Title: Concise and powerful, reflecting the core theme
2. Clear Structure: Include timeline, key viewpoints, in-depth analysis, and impact assessment
3. Length: 200-300 words
4. Language: {target_language}
5. Style: Professional, objective, and insightful

Please generate an article based on the following news entries:

{articles_info}

Output format as JSON:
```json
{
  "title": "Article Title",
  "summary": "Article summary (within 50 words)",
  "content": "Complete article content (Markdown format)",
  "keywords": ["keyword1", "keyword2", "keyword3"]
}
```
"""

        # 系统提示词
        self.default_system_prompt = """
You are a professional content analyst and writer who excels at integrating multiple related news stories into high-quality analytical articles.

Your articles have the following characteristics:
- Accurately extract core information
- Provide unique insights and analysis
- Clear structure and rigorous logic
- Concise language and powerful expression

Your role:
- Act as a senior journalist with extensive experience
- Focus on delivering objective, data-driven analysis
- Maintain professional standards while ensuring readability
- Synthesize information from multiple sources effectively
"""
    
    def generate_article(self, cluster_suggestion: ClusterSuggestion, 
                        digest: Digest) -> Optional[DigestArticle]:
        """根据聚类建议生成文章"""
        try:
            if not self.ai_agent:
                logger.error("没有配置AI代理，无法生成文章")
                return None
            
            # 准备文章信息
            articles_info = self._prepare_articles_info(cluster_suggestion.entries)
            
            # 构建提示词
            article_prompt = (digest.article_prompt or self.default_article_prompt).format(
                target_language=self.target_language,
                articles_info=articles_info
            )
            
            system_prompt = digest.system_prompt or self.default_system_prompt
            
            # 调用AI生成文章
            logger.info(f"开始生成文章: {cluster_suggestion.title}")
            result = self.ai_agent.completions(
                text=article_prompt,
                system_prompt=system_prompt
            )
            
            if not result.get('text'):
                logger.error("AI返回空结果")
                return None
            
            # 解析生成的文章
            article_data = self._parse_article_result(result['text'])
            if not article_data:
                logger.error("文章解析失败")
                return None
            
            # 创建DigestArticle对象
            article = DigestArticle(
                digest=digest,
                title=article_data.get('title', cluster_suggestion.title),
                summary=article_data.get('summary', cluster_suggestion.summary),
                content=article_data.get('content', ''),
                cluster_id=cluster_suggestion.cluster_id,
                cluster_keywords=cluster_suggestion.keywords,
                tokens_used=result.get('tokens', 0)
            )
            
            # 计算质量评分
            article.quality_score = self._calculate_article_quality(article_data, cluster_suggestion)
            
            # 保存文章
            article.save()
            
            # 添加来源条目关联
            article.source_entries.set(cluster_suggestion.entries)
            
            # 根据质量评分决定是否自动发布
            if article.quality_score >= 0.7:
                article.publish()
                logger.info(f"文章质量评分 {article.quality_score:.2f}，自动发布")
            else:
                logger.info(f"文章质量评分 {article.quality_score:.2f}，保存为草稿")
            
            logger.info(f"成功生成文章: {article.title}")
            return article
            
        except Exception as e:
            logger.error(f"生成文章失败: {e}")
            return None
    
    def _prepare_articles_info(self, entries: List[Entry]) -> str:
        """准备文章信息文本"""
        articles_text = []
        
        for i, entry in enumerate(entries, 1):
            title = entry.translated_title or entry.original_title or "无标题"
            content = entry.translated_content or entry.original_content or ""
            summary = entry.ai_summary or ""
            
            # 优先使用摘要，如果没有则使用内容前200字符
            description = summary if summary else content[:200]
            
            # 格式化文章信息
            article_info = f"""
文章 {i}:
标题: {title}
来源: {entry.feed.name if entry.feed else '未知来源'}
发布时间: {entry.pubdate.strftime('%Y-%m-%d %H:%M') if entry.pubdate else '未知时间'}
内容摘要: {description}
链接: {entry.link}
"""
            articles_text.append(article_info.strip())
        
        return "\n\n".join(articles_text)
    
    def _parse_article_result(self, ai_result: str) -> Optional[Dict[str, Any]]:
        """解析AI生成的文章结果"""
        try:
            # 提取JSON部分
            json_start = ai_result.find('```json')
            json_end = ai_result.find('```', json_start + 1)
            
            if json_start != -1 and json_end != -1:
                json_text = ai_result[json_start + 7:json_end].strip()
            else:
                # 尝试直接解析整个结果
                json_text = ai_result.strip()
            
            data = json.loads(json_text)
            
            # 验证必要字段
            required_fields = ['title', 'content']
            for field in required_fields:
                if field not in data or not data[field]:
                    logger.error(f"缺少必要字段: {field}")
                    return None
            
            return data
            
        except json.JSONDecodeError as e:
            logger.error(f"JSON解析失败: {e}")
            logger.debug(f"原始结果: {ai_result}")
            return None
        except Exception as e:
            logger.error(f"解析文章结果失败: {e}")
            return None
    
    def _calculate_article_quality(self, article_data: Dict[str, Any], 
                                 cluster_suggestion: ClusterSuggestion) -> float:
        """计算文章质量评分"""
        try:
            score = 0.0
            
            # 标题质量评分 (0.2)
            title = article_data.get('title', '')
            if title:
                if 10 <= len(title) <= 50:  # 合理的标题长度
                    score += 0.2
                elif 5 <= len(title) <= 80:
                    score += 0.1
            
            # 内容质量评分 (0.4)
            content = article_data.get('content', '')
            if content:
                content_length = len(content)
                if 200 <= content_length <= 500:  # 目标长度范围
                    score += 0.4
                elif 100 <= content_length <= 800:
                    score += 0.3
                elif content_length >= 50:
                    score += 0.2
            
            # 结构完整性评分 (0.2)
            if article_data.get('summary'):
                score += 0.1
            if article_data.get('keywords') and len(article_data['keywords']) >= 2:
                score += 0.1
            
            # 来源多样性评分 (0.2)
            sources = set([entry.feed.name for entry in cluster_suggestion.entries if entry.feed])
            if len(sources) >= 3:
                score += 0.2
            elif len(sources) >= 2:
                score += 0.15
            elif len(sources) >= 1:
                score += 0.1
            
            return min(1.0, score)
            
        except Exception as e:
            logger.error(f"质量评分计算失败: {e}")
            return 0.5
    
    def generate_digest_articles(self, digest: Digest, 
                               cluster_suggestions: List[ClusterSuggestion]) -> List[DigestArticle]:
        """批量生成日报文章"""
        try:
            generated_articles = []
            total_tokens = 0
            
            # 按质量评分排序，优先生成高质量聚类的文章
            sorted_suggestions = sorted(cluster_suggestions, 
                                      key=lambda x: x.quality_score, reverse=True)
            
            # 限制文章数量 - 改为AI自主决定，默认限制为10篇
            max_articles = 10  # 可以根据实际需要调整
            processed_count = 0
            
            for suggestion in sorted_suggestions[:max_articles]:
                if processed_count >= max_articles:
                    break
                
                article = self.generate_article(suggestion, digest)
                if article:
                    generated_articles.append(article)
                    total_tokens += article.tokens_used
                    processed_count += 1
                    
                    logger.info(f"生成第 {processed_count} 篇文章: {article.title}")
            
            # 更新日报的token统计
            digest.total_tokens += total_tokens
            digest.last_generated = timezone.now()
            digest.save()
            
            logger.info(f"批量生成完成: {len(generated_articles)} 篇文章，消耗 {total_tokens} tokens")
            return generated_articles
            
        except Exception as e:
            logger.error(f"批量生成文章失败: {e}")
            return []
    
    def regenerate_article(self, article: DigestArticle) -> bool:
        """重新生成文章"""
        try:
            # 重新创建聚类建议
            suggestion = ClusterSuggestion(
                cluster_id=article.cluster_id,
                title=article.title,
                keywords=article.cluster_keywords,
                entries=list(article.source_entries.all()),
                quality_score=article.quality_score,
                summary=article.summary
            )
            
            # 生成新文章
            new_article = self.generate_article(suggestion, article.digest)
            if new_article:
                # 替换原文章内容
                article.title = new_article.title
                article.summary = new_article.summary
                article.content = new_article.content
                article.quality_score = new_article.quality_score
                article.tokens_used += new_article.tokens_used
                article.save()
                
                # 删除临时新文章
                new_article.delete()
                
                logger.info(f"重新生成文章成功: {article.title}")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"重新生成文章失败: {e}")
            return False
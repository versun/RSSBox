"""
内容聚类服务
基于语义相似性对文章进行主题聚类，支持传统机器学习和AI智能聚类两种方法
"""

import json
import logging
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime, timedelta

import re

from django.utils import timezone
from core.models.entry import Entry
from core.models.tag import Tag
from utils.text_handler import clean_content

logger = logging.getLogger(__name__)


@dataclass
class ClusterSuggestion:
    """聚类建议数据类"""
    cluster_id: int
    title: str
    keywords: List[str]
    entries: List[Entry]
    quality_score: float
    summary: str


class EntryClusteringService:
    """文章聚类服务类"""
    
    def __init__(self, ai_agent=None):
        self.ai_agent = ai_agent  # AI代理用于智能聚类
        
        # AI聚类提示词模板
        self.clustering_prompt = """
你是一个专业的内容分析师，需要对以下文章进行主题聚类分组。

请根据文章内容的主题相似性，将它们分成若干个组别。每个组对应一个明确的主题，生成一篇综合分析文章。

请自主决定最佳的聚类数量，以确保：
1. 每个主题都有足够的内容深度
2. 不同主题之间有明显区分
3. 每个聚类都能生成有意义的综合分析

对于每个组，请提供：
1. 主题名称（简洁有力）
2. 关键词（5个以内）
3. 包含的文章ID列表
4. 主题摘要（200字以内）

输出格式为JSON：
```json
{
  "clusters": [
    {
      "id": 0,
      "title": "主题名称",
      "keywords": ["关键词1", "关键词2"],
      "entry_ids": [1, 2, 3],
      "summary": "主题摘要"
    }
  ]
}
```
"""
    def ai_clustering(self, entries: List[Entry]) -> Dict[int, List[Entry]]:
        """使用AI进行智能聚类"""
        if not self.ai_agent:
            logger.error("没有配置AI代理，无法进行聚类")
            return {0: entries}
        
        if len(entries) < 2:  # 至少阀2篇文章才进行聚类
            return {0: entries}
        
        try:
            # 准备文章数据
            articles_data = []
            for i, entry in enumerate(entries):
                title = entry.translated_title or entry.original_title or ""
                content = entry.translated_content or entry.original_content or ""
                # 只取内容的前500字符以控制token消耗
                summary_content = content[:500] if content else ""
                
                articles_data.append({
                    "id": i,
                    "title": title,
                    "content": summary_content,
                    "source": entry.feed.name if entry.feed else "未知来源"
                })
            
            # 构建提示词（不再传入限制参数）
            articles_text = "\n\n".join([
                f"ID: {article['id']}\n标题: {article['title']}\n内容: {article['content']}\n来源: {article['source']}"
                for article in articles_data
            ])
            
            prompt = self.clustering_prompt
            
            # 调用AI进行聚类
            logger.info(f"开始使用AI聚类 {len(entries)} 篇文章")
            result = self.ai_agent.completions(
                text=articles_text,
                system_prompt=prompt
            )
            
            if not result.get('text'):
                logger.error("AI聚类返回空结果")
                return self._fallback_clustering(entries)
            
            # 解析AI返回的JSON结果
            clusters = self._parse_ai_clustering_result(result['text'], entries)
            
            if not clusters:
                logger.error("AI聚类结果解析失败")
                return {0: entries}
            
            logger.info(f"AI聚类完成，生成 {len(clusters)} 个聚类")
            return clusters
            
        except Exception as e:
            logger.error(f"AI聚类失败: {e}")
            return {0: entries}
    
    def _parse_ai_clustering_result(self, ai_result: str, entries: List[Entry]) -> Dict[int, List[Entry]]:
        """解析AI聚类结果"""
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
            
            if 'clusters' not in data:
                logger.error("AI结果中缺少clusters字段")
                return {}
            
            clusters = {}
            for cluster_data in data['clusters']:
                cluster_id = cluster_data.get('id', 0)
                entry_ids = cluster_data.get('entry_ids', [])
                
                # 根据ID获取对应的Entry对象
                cluster_entries = []
                for entry_id in entry_ids:
                    if 0 <= entry_id < len(entries):
                        cluster_entries.append(entries[entry_id])
                
                # AI自主决定聚类，不再限制最小大小
                if len(cluster_entries) >= 1:  # 只要有文章就可以形成聚类
                    clusters[cluster_id] = cluster_entries
                    
                    # 保存AI生成的元数据
                    for entry in cluster_entries:
                        if not hasattr(entry, '_ai_cluster_metadata'):
                            entry._ai_cluster_metadata = {}
                        entry._ai_cluster_metadata.update({
                            'title': cluster_data.get('title', ''),
                            'keywords': cluster_data.get('keywords', []),
                            'summary': cluster_data.get('summary', '')
                        })
            
            return clusters
            
        except json.JSONDecodeError as e:
            logger.error(f"JSON解析失败: {e}")
            logger.debug(f"原始结果: {ai_result}")
            return {}
        except Exception as e:
            logger.error(f"解析AI结果失败: {e}")
            return {}
    

    def get_entries_from_tags(self, tags: List[Tag], hours_back: int = 24) -> List[Entry]:
        """从指定标签获取最近的文章条目"""
        try:
            # 计算时间范围
            since_time = timezone.now() - timedelta(hours=hours_back)
            
            # 获取所有相关的Feed
            feeds = []
            for tag in tags:
                feeds.extend(tag.feeds.all())
            
            # 去重
            feed_ids = list(set([feed.id for feed in feeds]))
            
            # 获取Entry
            entries = Entry.objects.filter(
                feed_id__in=feed_ids,
                pubdate__gte=since_time
            ).select_related('feed').order_by('-pubdate')
            
            logger.info(f"从 {len(tags)} 个标签获取到 {entries.count()} 个条目")
            return list(entries)
            
        except Exception as e:
            logger.error(f"获取条目失败: {e}")
            return []
    
    def preprocess_text(self, text: str) -> str:
        """多语言文本预处理，使用text_handler的clean_content函数"""
        if not text:
            return ""
        
        # 使用clean_content清理HTML并转换为markdown
        cleaned_text = clean_content(text)
        
        # 进一步清理特殊字符，保留多语言字符
        # 保留：英文、数字、中文、日文、韩文、阿拉伯文、西里尔文、拉丁扩展字符
        cleaned_text = re.sub(r'[^\w\s\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af\u0600-\u06ff\u0400-\u04ff\u0100-\u017f]', ' ', cleaned_text)
        
        # 规范化空白字符
        cleaned_text = re.sub(r'\s+', ' ', cleaned_text).strip()
        
        # 转换为小写（仅对拉丁字符有效，不影响其他语言）
        cleaned_text = cleaned_text.lower()
        
        return cleaned_text
    
    def generate_cluster_title(self, keywords: List[str], entries: List[Entry]) -> str:
        """生成聚类标题"""
        if not keywords:
            return f"主题聚类 ({len(entries)}篇文章)"
        
        # 使用前2个关键词生成标题
        main_keywords = keywords[:2]
        title = " + ".join(main_keywords)
        
        return f"{title} ({len(entries)}篇文章)"
    
    def calculate_cluster_quality(self, cluster_entries: List[Entry]) -> float:
        """计算聚类质量评分"""
        try:
            score = 0.0
            
            # 基于文章数量的评分
            size_score = min(1.0, len(cluster_entries) / 5.0)
            score += size_score * 0.3
            
            # 基于时间分布的评分
            pub_dates = [entry.pubdate for entry in cluster_entries if entry.pubdate]
            if pub_dates:
                time_span = (max(pub_dates) - min(pub_dates)).total_seconds() / 3600  # 小时
                time_score = 1.0 if time_span < 24 else max(0.5, 1.0 - time_span / 48.0)
                score += time_score * 0.2
            
            # 基于来源多样性的评分
            sources = set([entry.feed.name for entry in cluster_entries if entry.feed])
            source_diversity = min(1.0, len(sources) / 3.0)
            score += source_diversity * 0.3
            
            # 基于内容质量的评分
            content_score = 0.0
            for entry in cluster_entries:
                if entry.translated_content or entry.original_content:
                    content_length = len(entry.translated_content or entry.original_content or "")
                    if content_length > 200:
                        content_score += 1.0
                    elif content_length > 100:
                        content_score += 0.5
            
            content_score = content_score / len(cluster_entries) if cluster_entries else 0
            score += content_score * 0.2
            
            return min(1.0, score)
            
        except Exception as e:
            logger.error(f"质量评分计算失败: {e}")
            return 0.5
    
    def generate_cluster_suggestions(self, tags: List[Tag], hours_back: int = 24) -> List[ClusterSuggestion]:
        """生成聚类建议（优先使用AI聚类）"""
        try:
            # 获取文章条目
            entries = self.get_entries_from_tags(tags, hours_back)
            if len(entries) < 2:  # 至少阀2篇文章
                logger.warning(f"条目数量不足: {len(entries)} < 2")
                return []
            
            # 使用AI聚类
            if not self.ai_agent:
                logger.error("需要配置AI代理才能进行聚类")
                return []
                
            logger.info("使用AI智能聚类")
            clusters = self.ai_clustering(entries)
            
            # 生成建议
            suggestions = []
            for cluster_id, cluster_entries in clusters.items():
                # 使用AI生成的元数据
                if hasattr(cluster_entries[0], '_ai_cluster_metadata'):
                    metadata = cluster_entries[0]._ai_cluster_metadata
                    title = metadata.get('title', f'主题聚类 {cluster_id}')
                    keywords = metadata.get('keywords', [])
                    summary = metadata.get('summary', '')
                else:
                    # 备用方法
                    title = f'主题聚类 {cluster_id} ({len(cluster_entries)}篇文章)'
                    keywords = []
                    summary = self._generate_cluster_summary(cluster_entries)
                
                # 计算质量评分
                quality_score = self.calculate_cluster_quality(cluster_entries)
                
                suggestion = ClusterSuggestion(
                    cluster_id=cluster_id,
                    title=title,
                    keywords=keywords,
                    entries=cluster_entries,
                    quality_score=quality_score,
                    summary=summary
                )
                
                suggestions.append(suggestion)
            
            # 按质量评分排序
            suggestions.sort(key=lambda x: x.quality_score, reverse=True)
            
            logger.info(f"生成了 {len(suggestions)} 个聚类建议")
            return suggestions
            
        except Exception as e:
            logger.error(f"生成聚类建议失败: {e}")
            return []
    
    def _generate_cluster_summary(self, entries: List[Entry]) -> str:
        """生成聚类摘要"""
        try:
            titles = []
            for entry in entries[:3]:  # 只取前3个
                title = entry.translated_title or entry.original_title or ""
                if title:
                    titles.append(title)
            
            if titles:
                return f"包含关于 {', '.join(titles[:2])} 等主题的 {len(entries)} 篇文章"
            else:
                return f"包含 {len(entries)} 篇相关文章"
                
        except Exception as e:
            logger.error(f"摘要生成失败: {e}")
            return f"包含 {len(entries)} 篇文章"
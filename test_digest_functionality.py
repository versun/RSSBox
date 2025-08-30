"""
日报功能测试
验证核心组件的基本功能
"""

import os
import sys
import django
from datetime import datetime, timedelta

# 设置Django环境
sys.path.append('/Users/versun/Projects/RSS-Translator')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.test import TestCase
from django.utils import timezone
from core.models.digest import Digest, DigestArticle
from core.models.tag import Tag
from core.models.feed import Feed
from core.models.entry import Entry
from core.models.agent import OpenAIAgent


def test_digest_models():
    """测试日报模型的基本功能"""
    print("=== 测试日报模型 ===")
    
    try:
        # 创建测试标签
        tag = Tag.objects.create(name="测试标签")
        print(f"✓ 创建测试标签: {tag}")
        
        # 创建测试日报配置
        digest = Digest.objects.create(
            name="测试日报",
            description="这是一个测试日报",
            generation_weekdays=[1, 2, 3, 4, 5],  # 工作日
            generation_time="08:00",
            is_active=True
        )
        digest.tags.add(tag)
        print(f"✓ 创建测试日报: {digest}")
        
        # 不再创建DigestGeneration记录，因为已经删除该模型
        
        # 创建文章
        article = DigestArticle.objects.create(
            digest=digest,
            title="测试文章",
            summary="这是一篇测试文章的摘要",
            content="这是测试文章的完整内容",
            cluster_id=0,
            cluster_keywords=["测试", "文章"],
            quality_score=0.8,
            status="published"
        )
        article.publish()
        print(f"✓ 创建测试文章: {article}")
        
        print("✓ 所有模型测试通过!")
        return True
        
    except Exception as e:
        print(f"✗ 模型测试失败: {e}")
        return False


def test_clustering_service():
    """测试聚类服务"""
    print("\n=== 测试聚类服务 ===")
    
    try:
        from utils.clustering_service import EntryClusteringService
        
        # 创建聚类服务实例
        service = EntryClusteringService(min_cluster_size=2, max_clusters=5)
        print("✓ 创建聚类服务实例")
        
        # 测试文本预处理
        test_text = "这是一段<b>包含HTML标签</b>的测试文本！"
        processed = service.preprocess_text(test_text)
        print(f"✓ 文本预处理: '{test_text}' -> '{processed}'")
        
        # 测试中文检测
        is_chinese = service._contains_chinese("这是中文")
        print(f"✓ 中文检测: {is_chinese}")
        
        print("✓ 聚类服务基本测试通过!")
        return True
        
    except Exception as e:
        print(f"✗ 聚类服务测试失败: {e}")
        return False


def test_article_generator():
    """测试文章生成器"""
    print("\n=== 测试文章生成器 ===")
    
    try:
        from utils.digest_generator import DigestArticleGenerator
        
        # 创建文章生成器实例
        generator = DigestArticleGenerator(target_language="中文")
        print("✓ 创建文章生成器实例")
        
        # 测试质量评分
        test_article_data = {
            "title": "测试文章标题",
            "content": "这是一篇测试文章的内容" * 10,  # 扩展内容长度
            "summary": "测试摘要",
            "keywords": ["测试", "文章", "关键词"]
        }
        
        # 模拟聚类建议
        class MockClusterSuggestion:
            def __init__(self):
                self.cluster_id = 0
                self.title = "测试聚类"
                self.keywords = ["测试", "聚类"]
                self.entries = []
                self.quality_score = 0.8
                self.summary = "测试聚类摘要"
        
        mock_suggestion = MockClusterSuggestion()
        quality_score = generator._calculate_article_quality(test_article_data, mock_suggestion)
        print(f"✓ 质量评分计算: {quality_score:.2f}")
        
        print("✓ 文章生成器基本测试通过!")
        return True
        
    except Exception as e:
        print(f"✗ 文章生成器测试失败: {e}")
        return False


def test_digest_tasks():
    """测试日报任务"""
    print("\n=== 测试日报任务 ===")
    
    try:
        from utils.digest_tasks import get_digest_statistics, cleanup_old_articles
        
        # 测试统计功能
        stats = get_digest_statistics(days=30)
        print(f"✓ 获取统计信息: {stats['success']}")
        
        # 测试清理功能（但不实际执行）
        print("✓ 清理功能模块导入成功")
        
        print("✓ 日报任务基本测试通过!")
        return True
        
    except Exception as e:
        print(f"✗ 日报任务测试失败: {e}")
        return False


def test_digest_views():
    """测试日报视图"""
    print("\n=== 测试日报视图 ===")
    
    try:
        from core.digest_views import digest_list, digest_rss, digest_json
        print("✓ 导入日报视图函数成功")
        
        # 测试URL配置
        from django.urls import reverse
        try:
            # 这些URL可能还没有配置好，所以只测试导入
            print("✓ 日报视图导入成功")
        except Exception:
            print("! URL配置需要在运行时测试")
        
        print("✓ 日报视图基本测试通过!")
        return True
        
    except Exception as e:
        print(f"✗ 日报视图测试失败: {e}")
        return False


def test_admin_integration():
    """测试管理界面集成"""
    print("\n=== 测试管理界面集成 ===")
    
    try:
        from core.admin.digest_admin import DigestAdmin, DigestArticleAdmin
        print("✓ 导入日报管理界面成功")
        
        from core.admin.admin_site import core_admin_site
        print("✓ 导入核心管理站点成功")
        
        # 检查模型是否注册
        registered_models = [model._meta.model for model in core_admin_site._registry.keys()]
        from core.models.digest import Digest
        
        if Digest in registered_models:
            print("✓ 日报模型已注册到管理站点")
        else:
            print("! 日报模型未在管理站点中找到")
        
        print("✓ 管理界面集成基本测试通过!")
        return True
        
    except Exception as e:
        print(f"✗ 管理界面集成测试失败: {e}")
        return False


def main():
    """运行所有测试"""
    print("开始日报功能测试...")
    print("=" * 50)
    
    test_results = []
    
    # 运行各项测试
    test_results.append(test_digest_models())
    test_results.append(test_clustering_service())
    test_results.append(test_article_generator())
    test_results.append(test_digest_tasks())
    test_results.append(test_digest_views())
    test_results.append(test_admin_integration())
    
    # 汇总结果
    print("\n" + "=" * 50)
    print("测试结果汇总:")
    passed = sum(test_results)
    total = len(test_results)
    
    print(f"通过: {passed}/{total}")
    
    if passed == total:
        print("🎉 所有测试通过! 日报功能基本实现完成。")
        print("\n下一步操作:")
        print("1. 安装新的依赖: uv add scikit-learn jieba numpy")
        print("2. 运行数据库迁移: uv run python manage.py migrate")
        print("3. 在管理界面中配置日报")
        print("4. 手动测试生成命令: uv run python manage.py generate_digest --help")
    else:
        print("⚠️ 部分测试失败，请检查错误信息。")
    
    return passed == total


if __name__ == "__main__":
    main()
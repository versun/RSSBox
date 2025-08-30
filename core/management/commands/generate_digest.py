"""
Django管理命令：生成日报
支持手动生成特定日报或自动生成所有到期的日报
"""

import logging
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from utils.digest_tasks import (
    generate_daily_digest,
    generate_all_active_digests,
    cleanup_old_generations,
    get_digest_statistics
)
from core.models.digest import Digest

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = '生成日报内容'

    def add_arguments(self, parser):
        parser.add_argument(
            '--digest-id',
            type=int,
            help='指定要生成的日报ID'
        )
        
        parser.add_argument(
            '--all',
            action='store_true',
            help='生成所有在当前小时配置的活跃日报'
        )
        
        parser.add_argument(
            '--force',
            action='store_true',
            help='强制重新生成（即使今天已生成）'
        )
        
        parser.add_argument(
            '--hour',
            type=int,
            help='指定小时数，用于生成特定小时的日报（配合--all使用）'
        )
        
        parser.add_argument(
            '--cleanup',
            type=int,
            metavar='DAYS',
            help='清理指定天数之前的旧记录'
        )
        
        parser.add_argument(
            '--stats',
            action='store_true',
            help='显示日报统计信息'
        )
        
        parser.add_argument(
            '--stats-days',
            type=int,
            default=30,
            help='统计信息的天数范围（默认30天）'
        )

    def handle(self, *args, **options):
        try:
            # 清理旧记录
            if options['cleanup']:
                days = options['cleanup']
                self.stdout.write(f"开始清理 {days} 天前的旧记录...")
                result = cleanup_old_generations(days)
                
                if result['success']:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"清理完成: 删除了 {result['generations_deleted']} 个生成记录"
                            f"和 {result['articles_deleted']} 篇文章"
                        )
                    )
                else:
                    self.stdout.write(
                        self.style.ERROR(f"清理失败: {result['message']}")
                    )
                return
            
            # 显示统计信息
            if options['stats']:
                digest_id = options.get('digest_id')
                days = options['stats_days']
                
                self.stdout.write(f"获取最近 {days} 天的统计信息...")
                result = get_digest_statistics(digest_id, days)
                
                if result['success']:
                    self._display_statistics(result)
                else:
                    self.stdout.write(
                        self.style.ERROR(f"获取统计信息失败: {result['message']}")
                    )
                return
            
            # 生成所有活跃日报
            if options['all']:
                hour = options.get('hour')
                if hour is None:
                    hour = timezone.now().hour
                
                self.stdout.write(f"开始生成小时 {hour} 的所有活跃日报...")
                result = generate_all_active_digests(hour)
                
                if result['success']:
                    self._display_batch_results(result)
                else:
                    self.stdout.write(
                        self.style.ERROR(f"批量生成失败: {result['message']}")
                    )
                return
            
            # 生成特定日报
            if options['digest_id']:
                digest_id = options['digest_id']
                force = options['force']
                
                try:
                    digest = Digest.objects.get(id=digest_id)
                    self.stdout.write(f"开始生成日报: {digest.name}")
                    
                    result = generate_daily_digest(digest_id, force)
                    
                    if result['success']:
                        self._display_single_result(result, digest.name)
                    else:
                        self.stdout.write(
                            self.style.ERROR(f"生成失败: {result['message']}")
                        )
                
                except Digest.DoesNotExist:
                    raise CommandError(f"日报配置不存在: {digest_id}")
                
                return
            
            # 如果没有指定任何操作，显示帮助信息
            self.stdout.write("请指定操作:")
            self.stdout.write("  --digest-id <ID>  生成特定日报")
            self.stdout.write("  --all             生成所有当前小时的活跃日报")
            self.stdout.write("  --cleanup <DAYS>  清理旧记录")
            self.stdout.write("  --stats           显示统计信息")
            self.stdout.write("使用 --help 查看详细帮助")
            
        except Exception as e:
            logger.error(f"命令执行失败: {e}")
            raise CommandError(f"执行失败: {e}")

    def _display_single_result(self, result, digest_name):
        """显示单个日报生成结果"""
        self.stdout.write(
            self.style.SUCCESS(f"✓ {digest_name} - {result['message']}")
        )
        
        if 'articles_count' in result:
            self.stdout.write(f"  生成文章数: {result['articles_count']}")
        
        if 'tokens_used' in result:
            self.stdout.write(f"  Token消耗: {result['tokens_used']}")
        
        if 'generation_time' in result:
            self.stdout.write(f"  生成耗时: {result['generation_time']:.2f}秒")

    def _display_batch_results(self, result):
        """显示批量生成结果"""
        self.stdout.write(
            self.style.SUCCESS(
                f"批量生成完成: 总数 {result['total_digests']}, "
                f"成功 {result['successful']}, 失败 {result['failed']}"
            )
        )
        
        if result['results']:
            self.stdout.write("\n详细结果:")
            for res in result['results']:
                status = "✓" if res['success'] else "✗"
                style = self.style.SUCCESS if res['success'] else self.style.ERROR
                
                message = f"  {status} {res['digest_name']} - {res['message']}"
                
                if res['success'] and 'articles_count' in res:
                    message += f" ({res['articles_count']}篇文章)"
                
                self.stdout.write(style(message))

    def _display_statistics(self, stats):
        """显示统计信息"""
        self.stdout.write(self.style.SUCCESS(f"=== 日报统计信息 (最近{stats['period_days']}天) ==="))
        
        self.stdout.write(f"生成记录:")
        self.stdout.write(f"  总数: {stats['total_generations']}")
        self.stdout.write(f"  成功: {stats['successful_generations']}")
        self.stdout.write(f"  失败: {stats['failed_generations']}")
        self.stdout.write(f"  成功率: {stats['success_rate']:.1%}")
        
        self.stdout.write(f"\n文章统计:")
        self.stdout.write(f"  总数: {stats['total_articles']}")
        self.stdout.write(f"  已发布: {stats['published_articles']}")
        self.stdout.write(f"  发布率: {stats['publish_rate']:.1%}")
        self.stdout.write(f"  平均每次生成: {stats['avg_articles_per_generation']:.1f}篇")
        
        self.stdout.write(f"\nToken统计:")
        self.stdout.write(f"  总消耗: {stats['total_tokens_used']:,}")
        
        if stats['total_generations'] > 0:
            avg_tokens = stats['total_tokens_used'] / stats['total_generations']
            self.stdout.write(f"  平均每次: {avg_tokens:.0f}")
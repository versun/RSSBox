import logging
import sys
import time
from django.core.management.base import BaseCommand
from core.models import Feed
from core.tasks import handle_single_feed_fetch, handle_feeds_translation, handle_feeds_summary
from django.db import close_old_connections
from utils.task_manager import task_manager
from core.cache import cache_rss, cache_category


class Command(BaseCommand):
    help = 'Updates feeds based on specified frequency or runs immediate update'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--frequency', 
            type=str,
            nargs='?',  # 可选参数
            help="Specify update frequency ('5 min', '15 min', '30 min', 'hourly', 'daily', 'weekly')"
        )
    
    def handle(self, *args, **options):
        target_frequency = options['frequency']
        
        if target_frequency:
            valid_frequencies = ['5 min', '15 min', '30 min', 'hourly', 'daily', 'weekly']
            if target_frequency not in valid_frequencies:
                self.stderr.write(f"{time.time()}: Error: Invalid frequency. Valid options: {', '.join(valid_frequencies)}")
                sys.exit(1)
            try:
                update_feeds_for_frequency(simple_update_frequency=target_frequency)
                self.stdout.write(self.style.SUCCESS(
                    f'{time.time()}: Successfully updated feeds for frequency: {target_frequency}'
                ))
            except Exception as e:
                logging.exception(f"Command update_feeds_for_frequency failed: {str(e)}")
                self.stderr.write(self.style.ERROR(f'Error: {str(e)}'))
                sys.exit(1)


def update_single_feed(feed:Feed):
    """在后台线程中执行feed更新"""        
    try:
        # 确保在新线程中创建新的数据库连接
        close_old_connections()
        
        try:
            
            logging.info(f"Starting feed update: {feed.name}")

            handle_single_feed_fetch(feed)
            #task_manager.update_progress(feed_id, 50)
            # 执行更新操作
            if feed.translate_title:
                handle_feeds_translation([feed], target_field="title")
            if feed.translate_content:
                handle_feeds_translation([feed], target_field="content")
            if feed.summary:
                handle_feeds_summary([feed])
            
            logging.info(f"Completed feed update: {feed.name}")

            return True
        except Feed.DoesNotExist:
            logging.error(f"Feed not found: ID {feed.name}")
            return False
        except Exception as e:
            logging.exception(f"Error updating feed ID {feed.name}: {str(e)}")
            return False
    finally:
        # 确保关闭数据库连接
        close_old_connections()

def update_multiple_feeds(feeds: list):
    """并行更新多个Feed"""
    try:
        # 先执行所有feed更新任务
        task_ids = []
        for feed in feeds:
            task_name = f"update_feed_{feed.name}"
            task_id = task_manager.submit_task(task_name, update_single_feed, feed)
            task_ids.append(task_id)
        
        # 等待所有任务完成（最多30分钟）
        timeout = 1800  # 30分钟（秒）
        start_time = time.time()
        
        while True:
            # 检查所有任务状态
            all_done = True
            for task_id in task_ids:
                status_info = task_manager.get_task_status(task_id)
                if status_info.get('status') in ['pending', 'running']:
                    all_done = False
                    break
            
            # 如果所有任务完成或超时则退出循环
            if all_done or (time.time() - start_time > timeout):
                break
            
            # 等待1秒后再次检查
            time.sleep(3)
        
        # 所有任务完成后执行缓存操作
        for feed in feeds:
            try:
                cache_rss(feed.slug, feed_type="o", format="xml")
                cache_rss(feed.slug, feed_type="o", format="json")
                cache_rss(feed.slug, feed_type="t", format="xml")
                cache_rss(feed.slug, feed_type="t", format="json")
            except Exception as e:
                logging.error(f"{time.time()}: Failed to cache RSS for {feed.slug}: {str(e)}")
        
        categories = set(feed.category for feed in feeds if feed.category)
        for category in categories:
            try:
                cache_category(category, feed_type="o", format="xml")
                cache_category(category, feed_type="t", format="xml")
                cache_category(category, feed_type="t", format="json")
            except Exception as e:
                logging.error(f"Failed to cache category {category}: {str(e)}")
                
    except Exception as e:
        logging.exception("Command update_multiple_feeds failed: %s", str(e))

def update_feeds_for_frequency(simple_update_frequency: str):
    """
    Update feeds for given update frequency group.
    """
    update_frequency_map = {
    "5 min": 5,
    "15 min": 15,
    "30 min": 30,
    "hourly": 60,
    "daily": 1440,
    "weekly": 10080,
    }
    
    try:
        frequency_val = update_frequency_map[simple_update_frequency]
        feeds = list(Feed.objects.filter(update_frequency=frequency_val))
        log = f"{time.time()}: Start update feeds for frequency: {simple_update_frequency}, feeds count: {len(feeds)}"
        logging.info(log)
        # output to stdout
        print(log)
        update_multiple_feeds(feeds) if feeds else logging.info("No feeds to update for this frequency.")
    except KeyError:
        logging.error(f"Invalid frequency: {simple_update_frequency}")
    except Exception as e:
        log = f"{time.time()}: Command update_feeds_for_frequency {simple_update_frequency}: {str(e)}"
        logging.exception(log)
        print(log)

# if __name__ == "__main__":
#     if len(sys.argv) > 1:
#         target_frequency = sys.argv[1]
#     else:
#         print("Error: Please specify a valid update frequency ('5 min', '15 min', '30 min', 'hourly', 'daily', 'weekly')")
#         sys.exit(1)
    
#     update_feeds_for_frequency(simple_update_frequency=target_frequency)
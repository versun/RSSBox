import logging
import sys
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
                self.stderr.write(f"Error: Invalid frequency. Valid options: {', '.join(valid_frequencies)}")
                sys.exit(1)
            try:
                update_feeds_for_frequency(simple_update_frequency=target_frequency)
                self.stdout.write(self.style.SUCCESS(
                    f'Successfully updated feeds for frequency: {target_frequency}'
                ))
            except Exception as e:
                logging.exception(f"Command update_feeds_for_frequency failed: {str(e)}")
                self.stderr.write(self.style.ERROR(f'Error: {str(e)}'))
                sys.exit(1)


def update_single_feed(feed_id):
    """在后台线程中执行feed更新"""        
    try:
        # 确保在新线程中创建新的数据库连接
        close_old_connections()
        need_cache = False
        
        try:
            # 尝试获取feed对象
            feed = Feed.objects.get(id=feed_id)
            logging.info(f"Starting feed update: {feed.name} (ID: {feed_id})")

            if handle_single_feed_fetch(feed):
                need_cache = True
            #task_manager.update_progress(feed_id, 50)
            # 执行更新操作
            if feed.translate_title:
                handle_feeds_translation([feed], target_field="title")
            if feed.translate_content:
                handle_feeds_translation([feed], target_field="content")
            if feed.summary:
                handle_feeds_summary([feed])
            
            logging.info(f"Completed feed update: {feed.name} (ID: {feed_id})")
            if need_cache:
                cache_rss(feed.slug, feed_type="o", formate="xml")
                cache_rss(feed.slug, feed_type="o", formate="json")
                cache_rss(feed.slug, feed_type="t", formate="xml")
                cache_rss(feed.slug, feed_type="t", formate="json")
                if feed.category:
                    cache_category(feed.category, feed_type="o", formate="xml")
                    cache_category(feed.category, feed_type="o", formate="json")
                    cache_category(feed.category, feed_type="t", formate="xml")
                    cache_category(feed.category, feed_type="t", formate="json")


            return True
        except Feed.DoesNotExist:
            logging.error(f"Feed not found: ID {feed_id}")
            return False
        except Exception as e:
            logging.exception(f"Error updating feed ID {feed_id}: {str(e)}")
            return False
    finally:
        # 确保关闭数据库连接
        close_old_connections()

def update_multiple_feeds(feeds: list):
    """并行更新多个Feed"""
    try:
        # 为每个feed创建并行任务
        task_ids = []
        for feed in feeds:
            task_name = f"update_feed_{feed.id}"
            task_id = task_manager.submit_task(task_name, update_single_feed, feed.id)
            task_ids.append(task_id)
        
        # 等待所有任务完成（可选，根据需求决定是否阻塞）
        # 实际应用中可能需要更完善的等待/超时机制
        # while True:
        #     completed = all(
        #         task_manager.get_task_status(tid)['status'] in ['completed', 'failed']
        #         for tid in task_ids
        #     )
        #     if completed:
        #         break
        #     time.sleep(3)  # 避免CPU忙等待
            
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
        log = f"Start update feeds for frequency: {simple_update_frequency}, feeds count: {len(feeds)}"
        logging.info(log)
        # output to stdout
        print(log)
        update_multiple_feeds(feeds)
    except KeyError:
        logging.error(f"Invalid frequency: {simple_update_frequency}")
    except Exception as e:
        log = f"Command update_feeds_for_frequency {simple_update_frequency}: {str(e)}"
        logging.exception(log)
        print(log)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        target_frequency = sys.argv[1]
    else:
        print("Error: Please specify a valid update frequency ('5 min', '15 min', '30 min', 'hourly', 'daily', 'weekly')")
        sys.exit(1)
    
    update_feeds_for_frequency(simple_update_frequency=target_frequency)
import logging
import sys
from itertools import chain
import time
import os
from concurrent.futures import wait

from django.core.management.base import BaseCommand
from core.models import Feed, Tag
from core.tasks import (
    handle_single_feed_fetch,
    handle_feeds_translation,
    handle_feeds_summary,
)
from django.db import close_old_connections
from utils.task_manager import task_manager
from core.cache import cache_rss, cache_tag

current_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time()))

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Updates feeds based on specified frequency or runs immediate update"

    def add_arguments(self, parser):
        parser.add_argument(
            "--frequency",
            type=str,
            nargs="?",  # 可选参数
            help="Specify update frequency ('5 min', '15 min', '30 min', 'hourly', 'daily', 'weekly')",
        )

    def handle(self, *args, **options):
        target_frequency = options["frequency"]
        if not target_frequency:
            self.stderr.write(f"{current_time}: Error: Frequency must be specified.")
            sys.exit(1)

        valid_frequencies = [
            "5 min",
            "15 min",
            "30 min",
            "hourly",
            "daily",
            "weekly",
        ]
        if target_frequency not in valid_frequencies:
            self.stderr.write(
                f"{current_time}: Error: Invalid frequency. Valid options: {', '.join(valid_frequencies)}"
            )
            sys.exit(1)

        lock_file_path = f"/tmp/update_feeds_{target_frequency.replace(' ', '_')}.lock"

        if os.path.exists(lock_file_path):
            self.stdout.write(
                self.style.WARNING(
                    f"{current_time}: Another update process for frequency '{target_frequency}' is already running. Exiting."
                )
            )
            sys.exit(0)

        try:
            # Create lock file
            with open(lock_file_path, "w") as f:
                f.write(str(os.getpid()))

            update_feeds_for_frequency(simple_update_frequency=target_frequency)
            self.stdout.write(
                self.style.SUCCESS(
                    f"{current_time}: Successfully updated feeds for frequency: {target_frequency}"
                )
            )
        except Exception as e:
            logger.exception(f"Command update_feeds_for_frequency failed: {str(e)}")
            self.stderr.write(self.style.ERROR(f"Error: {str(e)}"))
            sys.exit(1)
        finally:
            # Ensure lock file is removed
            if os.path.exists(lock_file_path):
                os.remove(lock_file_path)


def update_single_feed(feed: Feed):
    """在后台线程中执行feed更新"""
    try:
        # 确保在新线程中创建新的数据库连接
        close_old_connections()

        try:
            logger.info(f"Starting feed update: {feed.name}")

            handle_single_feed_fetch(feed)
            # task_manager.update_progress(feed_id, 50)
            # 执行更新操作
            if feed.translate_title:
                handle_feeds_translation([feed], target_field="title")
            if feed.translate_content:
                handle_feeds_translation([feed], target_field="content")
            if feed.summary:
                handle_feeds_summary([feed])

            logger.info(f"Completed feed update: {feed.name}")

            return True
        except Feed.DoesNotExist:
            logger.error(f"Feed not found: ID {feed.name}")
            return False
        except Exception as e:
            logger.exception(f"Error updating feed ID {feed.name}: {str(e)}")
            return False
    finally:
        # 确保关闭数据库连接
        close_old_connections()


def update_multiple_feeds(feeds: list):
    """并行更新多个Feed"""
    if not feeds:
        logger.info("No feeds to update.")
        return
    try:
        # 先执行所有feed更新任务
        futures = [
            task_manager.submit_task(
                f"update_feed_{feed.name}", update_single_feed, feed
            )
            for feed in feeds
        ]

        # 等待所有任务完成（最多30分钟）
        timeout = 1800  # 30分钟（1800秒）
        done, not_done = wait(futures, timeout=timeout)

        if not_done:
            logger.warning(
                f"Feed update task timed out. {len(not_done)} tasks did not complete."
            )

        for future in done:
            try:
                future.result()
            except Exception as e:
                logger.warning(f"A feed update task resulted in an exception: {e}")

        # 所有任务完成后执行缓存操作
        # Note: 'feeds' is a list materialized from an iterator, so it's safe to iterate again.
        for feed in feeds:
            try:
                cache_rss(feed.slug, feed_type="o", format="xml")
                cache_rss(feed.slug, feed_type="o", format="json")
                cache_rss(feed.slug, feed_type="t", format="xml")
                cache_rss(feed.slug, feed_type="t", format="json")
            except Exception as e:
                logger.error(
                    f"{time.time()}: Failed to cache RSS for {feed.slug}: {str(e)}"
                )

        # 获取所有 feeds 关联的 tags（去重）
        tag_ids = set(
            chain.from_iterable(
                feed.tags.values_list("id", flat=True) for feed in feeds
            )
        )
        tags = Tag.objects.filter(id__in=tag_ids)
        for tag in tags:
            try:
                cache_tag(tag.slug, feed_type="o", format="xml")
                cache_tag(tag.slug, feed_type="t", format="xml")
                cache_tag(tag.slug, feed_type="t", format="json")
            except Exception as e:
                logger.error(f"Failed to cache tag {tag.slug}: {str(e)}")

    except Exception as e:
        logger.exception("Command update_multiple_feeds failed: %s", str(e))


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
        # Use iterator to reduce initial memory load, then convert to list for multiple uses.
        feeds_iterator = Feed.objects.filter(update_frequency=frequency_val).iterator()
        feeds_list = list(feeds_iterator)

        log = f"{current_time}: Start update feeds for frequency: {simple_update_frequency}, feeds count: {len(feeds_list)}"
        logger.info(log)
        # output to stdout
        print(log)

        update_multiple_feeds(feeds_list)

    except KeyError:
        logger.error(f"Invalid frequency: {simple_update_frequency}")
    except Exception as e:
        log = f"{current_time}: Command update_feeds_for_frequency {simple_update_frequency}: {str(e)}"
        logger.exception(log)
        print(log)


# if __name__ == "__main__":
#     if len(sys.argv) > 1:
#         target_frequency = sys.argv[1]
#     else:
#         print("Error: Please specify a valid update frequency ('5 min', '15 min', '30 min', 'hourly', 'daily', 'weekly')")
#         sys.exit(1)

#     update_feeds_for_frequency(simple_update_frequency=target_frequency)

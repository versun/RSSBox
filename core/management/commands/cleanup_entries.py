# Remove entries greater than feed.max_posts

import os
import sys
import time
import logging
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from core.models import Feed, Entry

current_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time()))


class Command(BaseCommand):
    help = "Clean up entries by removing those beyond each feed's max_posts limit"

    def handle(self, *args, **options):
        lock_file_path = "/tmp/cleanup_entries.lock"

        if os.path.exists(lock_file_path):
            self.stdout.write(
                self.style.WARNING(
                    f"{current_time}: Cleanup process is already running. Exiting."
                )
            )
            sys.exit(0)

        try:
            with open(lock_file_path, "w") as f:
                f.write(str(os.getpid()))

            cleanup_all_feeds()
            self.stdout.write(
                self.style.SUCCESS(f"{current_time}: Successfully cleaned up all feeds")
            )
        except Exception as e:
            logging.exception(f"Command cleanup_entries failed: {str(e)}")
            self.stderr.write(self.style.ERROR(f"Error: {str(e)}"))
            sys.exit(1)
        finally:
            if os.path.exists(lock_file_path):
                os.remove(lock_file_path)


def cleanup_feed_entries(feed: Feed):
    """Remove entries beyond the feed's max_posts limit"""
    try:
        close_old_connections()
        total_entries = feed.entries.count()

        if total_entries <= feed.max_posts:
            return

        # Get IDs of entries to keep (latest max_posts entries)
        keep_ids = list(
            feed.entries.order_by("-id").values_list("id", flat=True)[: feed.max_posts]
        )

        # Delete older entries
        deleted_count = feed.entries.exclude(id__in=keep_ids).delete()[0]
        logging.info(
            f"Cleaned {deleted_count} entries from {feed.name} "
            f"(kept {len(keep_ids)}/{total_entries})"
        )
    except Exception as e:
        logging.exception(f"Error cleaning feed {feed.name}: {str(e)}")
    finally:
        close_old_connections()


def cleanup_all_feeds():
    """Clean up entries for all feeds"""
    try:
        # Replace iterator with list to avoid connection issues
        feeds = list(Feed.objects.all())
        total_feeds = len(feeds)
        processed = 0

        for feed in feeds:
            processed += 1
            cleanup_feed_entries(feed)

            if processed % 10 == 0:
                logging.info(
                    f"{current_time}: Processing feed {processed}/{total_feeds}"
                )
                close_old_connections()  # Close connections after processing batch

        logging.info(f"{current_time}: Completed cleanup for {total_feeds} feeds")
    except Exception as e:
        logging.exception("cleanup_all_feeds failed: %s", str(e))
        raise

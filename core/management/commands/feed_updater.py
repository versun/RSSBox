import fcntl
import logging
import sys
from itertools import chain
import time
import os
from concurrent.futures import wait

from django.core.management.base import BaseCommand
from core.models import Feed, Tag
from core.tasks.fetch_feeds import handle_single_feed_fetch
from core.tasks.translate_feeds import handle_feeds_translation
from core.tasks.summarize_feeds import handle_feeds_summary
from django.db import close_old_connections
from core.tasks.task_manager import task_manager
from core.cache import cache_rss, cache_tag
from core.models.filter import bump_cache_version

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

        # Use a kernel-managed flock instead of a plain PID file: the lock is
        # released automatically when the process exits for ANY reason (crash,
        # OOM kill, container stop), so it can never go stale and block all
        # future runs of this frequency (issue #161).
        # Open without truncating: a competing run must not wipe the PID
        # written by the process that holds the lock.
        with open(lock_file_path, "a") as lock_file:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                self.stdout.write(
                    self.style.WARNING(
                        f"{current_time}: Another update process for frequency '{target_frequency}' is already running. Exiting."
                    )
                )
                sys.exit(0)

            # Record the holder PID for debugging only; the flock, not this
            # file's existence, is what actually excludes other processes.
            lock_file.truncate(0)
            lock_file.seek(0)
            lock_file.write(str(os.getpid()))
            lock_file.flush()

            try:
                ok = update_feeds_for_frequency(
                    simple_update_frequency=target_frequency
                )
                if ok is False:
                    self.stderr.write(
                        self.style.WARNING(
                            f"{current_time}: Finished with failed feeds for frequency: {target_frequency}"
                        )
                    )
                    sys.exit(2)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"{current_time}: Successfully updated feeds for frequency: {target_frequency}"
                    )
                )
            except Exception as e:
                logger.exception(f"Command update_feeds_for_frequency failed: {str(e)}")
                self.stderr.write(self.style.ERROR(f"Error: {str(e)}"))
                sys.exit(1)


def _feed_lock_path(feed) -> str:
    """Filesystem path of the per-feed update lock.

    ``feed.pk`` may be a Mock/None in tests; fall back to slug then name.
    """
    try:
        ident = feed.pk or feed.slug or feed.name
    except Exception:
        ident = id(feed)
    return f"/tmp/rssbox_feed_update_{ident}.lock"


def _try_acquire_feed_lock(feed):
    """A non-blocking per-feed flock, or None when another process holds it.

    The frequency flock in Command.handle only serializes CLI runs of one
    frequency group; an admin-triggered update (from the web process) or a
    different frequency group can still process the same feed concurrently
    — duplicated AI billing and last-writer clobbering. Holding a per-feed
    lock for the whole pipeline fixes that; it is kernel-released on
    close/exit so it can never go stale.

    A feed object that cannot be locked (a bare Mock in unit tests, or a
    filesystem that rejects the path) degrades to an always-acquired null
    lock rather than breaking the update.
    """
    lock_path = _feed_lock_path(feed)
    try:
        lock_file = open(lock_path, "a")
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        return None
    except Exception:
        logger.debug(
            "Could not take per-feed update lock %s; proceeding unlocked",
            lock_path,
        )
        return _NullFeedLock()
    return lock_file


class _NullFeedLock:
    """Context object returned when no real lock can be taken."""

    def close(self):
        pass


def update_single_feed(feed: Feed):
    """在后台线程中执行feed更新"""
    try:
        # 确保在新线程中创建新的数据库连接
        close_old_connections()

        feed_lock = _try_acquire_feed_lock(feed)
        if feed_lock is None:
            # The competing run is already doing (or just did) the same
            # work, so skipping is safe — and it is not a failure: the feed
            # still gets its update, just from the holder.
            logger.info(
                f"Skipping feed update for {feed.name}: another process "
                "is already updating this feed"
            )
            return True

        try:
            logger.info(f"Starting feed update: {feed.name}")

            handle_single_feed_fetch(feed)
            # A stale status (False or a leftover None) must not poison the
            # current run's bookkeeping; derive success only from the steps
            # that actually ran this time.
            completed = True
            # task_manager.update_progress(feed_id, 50)
            # 执行更新操作
            if feed.translate_title:
                handle_feeds_translation([feed], target_field="title")
                completed = (feed.translation_status is True) and completed
            if feed.translate_content:
                handle_feeds_translation([feed], target_field="content")
                completed = (feed.translation_status is True) and completed
            if feed.summary:
                handle_feeds_summary([feed])
                completed = (feed.translation_status is True) and completed

            if feed.translate_title or feed.translate_content or feed.summary:
                feed.translation_status = completed
                feed.save(update_fields=["translation_status"])

            # Entries changed underneath any previously rendered output;
            # bump so readers regenerate instead of waiting out the TTL.
            # update_multiple_feeds also bumps before its explicit regen,
            # which is fine — versions are cheap counters.
            if isinstance(getattr(feed, "pk", None), int):
                bump_cache_version("rss", feed.pk)
                for tag_pk in feed.tags.values_list("pk", flat=True):
                    bump_cache_version("tag", tag_pk)

            logger.info(f"Completed feed update: {feed.name}")

            return completed
        except Feed.DoesNotExist:
            logger.error(f"Feed not found: ID {feed.name}")
            return False
        except Exception as e:
            logger.exception(f"Error updating feed ID {feed.name}: {str(e)}")
            return False
        finally:
            feed_lock.close()
    finally:
        # 确保关闭数据库连接
        close_old_connections()


def update_multiple_feeds(feeds: list) -> bool:
    """并行更新多个Feed

    Returns True when every submitted feed updated without an AI-pipeline
    failure; False when at least one feed's update_single_feed returned
    False or raised.
    """
    if not feeds:
        logger.info("No feeds to update.")
        return True
    failed = []
    try:
        futures = []  # list of (feed, future); a submission error counts as failed
        try:
            submit_error = None
            for feed in feeds:
                try:
                    future = task_manager.submit_task(
                        f"update_feed_{feed.slug}", update_single_feed, feed
                    )
                    futures.append((feed, future))
                except Exception as e:
                    submit_error = e
                    failed.append(feed.name or feed.slug)
                    logger.warning(
                        f"Failed to submit update task for {feed.name or feed.slug}: {e}"
                    )
            # If every submission failed, this is a systemic failure (e.g.
            # the pool is shut down), not a per-feed problem; fail the batch.
            if not futures:
                raise submit_error

            _, not_done = wait([f for _, f in futures], timeout=1800)
            if not_done:
                stuck = [
                    f.name or f.slug for f, fut in futures if fut in not_done
                ]
                logger.warning(
                    f"Feed update timed out; {len(not_done)} tasks did not complete: "
                    f"{stuck}. Waiting for remaining workers to finish."
                )
        finally:
            # wait(timeout=...) does not stop running threads. Drain every
            # submitted task before returning to Command.handle, which owns
            # the frequency lock. Also cover a partial submission failure.
            #
            # A False result (or a raised exception) means that feed's AI
            # pipeline failed even though its fetch may have succeeded; make
            # the failures visible instead of letting the CLI report success.
            for feed, future in futures:
                try:
                    if future.result() is False:
                        failed.append(feed.name or feed.slug)
                except Exception as e:
                    failed.append(feed.name or feed.slug)
                    logger.warning(
                        f"A feed update task resulted in an exception: {e}"
                    )
            if failed:
                logger.warning(
                    "Feed update finished with %s failed feed(s): %s",
                    len(failed),
                    failed,
                )

        # 所有任务完成后执行缓存操作
        # Note: 'feeds' is a list materialized from an iterator, so it's safe to iterate again.
        # The versions must be bumped BEFORE regenerating: a reader between
        # the AI write and a later bump could otherwise repopulate the old
        # key and keep serving stale entries until its TTL.
        for feed in feeds:
            if not isinstance(getattr(feed, "pk", None), int):
                continue
            bump_cache_version("rss", feed.pk)
            for tag_pk in feed.tags.values_list("pk", flat=True):
                bump_cache_version("tag", tag_pk)
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

        return not failed
    except Exception as e:
        logger.exception("Command update_multiple_feeds failed: %s", str(e))
        return False


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
        # print(log)

        return update_multiple_feeds(feeds_list)

    except KeyError:
        logger.error(f"Invalid frequency: {simple_update_frequency}")
        return False
    except Exception as e:
        log = f"{current_time}: Command update_feeds_for_frequency {simple_update_frequency}: {str(e)}"
        logger.exception(log)
        return False


# if __name__ == "__main__":
#     if len(sys.argv) > 1:
#         target_frequency = sys.argv[1]
#     else:
#         print("Error: Please specify a valid update frequency ('5 min', '15 min', '30 min', 'hourly', 'daily', 'weekly')")
#         sys.exit(1)

#     update_feeds_for_frequency(simple_update_frequency=target_frequency)

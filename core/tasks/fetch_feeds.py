import logging
import time
from django.utils import timezone
from core.models import Feed, Entry
from typing import Dict
import feedparser
from fake_useragent import UserAgent

logger = logging.getLogger(__name__)


def handle_single_feed_fetch(feed: Feed):
    """
    Fetch feeds and update entries with batch processing optimization.
    """
    try:
        feed.fetch_status = None
        etag = feed.etag if feed.max_posts <= feed.entries.count() else ""
        fetch_results = fetch_feed(url=feed.feed_url, etag=etag)

        if fetch_results["error"]:
            raise Exception(f"Fetch Feed Failed: {fetch_results['error']}")
        elif not fetch_results["update"]:
            feed.fetch_status = True
            feed.log = f"{timezone.now()} Feed is up to date, Skip <br>"
            return

        latest_feed = fetch_results.get("feed")
        # Update feed meta
        feed_name_is_the_default = (
            feed.name is None or feed.name == "Loading" or feed.name == "Empty"
        )
        feed.name = (
            latest_feed.feed.get("title") if feed_name_is_the_default else feed.name
        )
        feed.subtitle = latest_feed.feed.get("subtitle")
        feed.language = latest_feed.feed.get("language")
        feed.author = latest_feed.feed.get("author") or "Unknown"
        feed.link = latest_feed.feed.get("link") or feed.feed_url
        feed.pubdate = convert_struct_time_to_datetime(
            latest_feed.feed.get("published_parsed")
        )
        feed.updated = convert_struct_time_to_datetime(
            latest_feed.feed.get("updated_parsed")
        )
        feed.last_fetch = timezone.now()
        feed.etag = latest_feed.get("etag")

        # Update entries
        if getattr(latest_feed, "entries", None):
            BATCH_SIZE = 50  # Smaller batch size for memory efficiency
            entries_to_create = []
            existing_entries = dict(
                Entry.objects.filter(feed=feed).values_list("guid", "id")
            )

            # Sort entries by publication date (newest first)
            sorted_entries = sorted(
                latest_feed.entries,
                key=lambda x: (
                    x.get("published_parsed")
                    or x.get("updated_parsed")
                    or time.gmtime(0)  # Fallback to epoch if no date
                ),
                reverse=True,
            )[: feed.max_posts]

            for i, entry_data in enumerate(sorted_entries):
                # Get content
                content = ""
                if "content" in entry_data:
                    content = (
                        entry_data.content[0].value or entry_data.content[1].value or ""
                    )
                else:
                    content = entry_data.get("summary")

                guid = entry_data.get("id") or entry_data.get("link")
                link = entry_data.get("link", "")
                author = entry_data.get("author", feed.author)
                if not guid:
                    continue  # Skip invalid entries

                # Create new entry if needed
                if guid not in existing_entries:
                    entry_values = {
                        "link": link,
                        "author": author,
                        "pubdate": convert_struct_time_to_datetime(
                            entry_data.get("published_parsed")
                        ),
                        "updated": convert_struct_time_to_datetime(
                            entry_data.get("updated_parsed")
                        ),
                        "original_title": entry_data.get("title", "No title"),
                        "original_content": content,
                        "original_summary": entry_data.get("summary"),
                        "enclosures_xml": entry_data.get("enclosures_xml"),
                    }

                    entries_to_create.append(
                        Entry(feed=feed, guid=guid, **entry_values)
                    )

                    # Batch create periodically
                    if len(entries_to_create) >= BATCH_SIZE:
                        Entry.objects.bulk_create(entries_to_create)
                        entries_to_create = []
                        # Explicitly clear memory
                        del entry_values
                        if "content" in locals():
                            del content

            # Create remaining entries
            if entries_to_create:
                Entry.objects.bulk_create(entries_to_create)
                del entries_to_create

        feed.fetch_status = True
        feed.log = f"{timezone.now()} Fetch Completed <br>"
    except Exception as e:
        logger.error("Task handle_single_feed_fetch %s: %s", feed.feed_url, str(e))
        feed.fetch_status = False
        feed.log = f"{timezone.now()} {str(e)}<br>"
    finally:
        feed.save()
        # Explicitly clean up large objects
        if "latest_feed" in locals():
            del latest_feed
        if "sorted_entries" in locals():
            del sorted_entries
        if "fetch_results" in locals():
            del fetch_results


def handle_feeds_fetch(feeds: list):
    """
    Fetch feeds and update entries with memory optimization.
    """
    for feed in feeds:
        handle_single_feed_fetch(feed)
        # Explicitly clean up reference
        del feed


def convert_struct_time_to_datetime(time_str):
    if not time_str:
        return None
    return timezone.datetime.fromtimestamp(
        time.mktime(time_str), tz=timezone.get_default_timezone()
    )


def manual_fetch_feed(url: str, etag: str = "") -> Dict:
    import httpx

    update = False
    feed = {}
    error = None
    response = None
    ua = UserAgent()
    headers = {
        "If-None-Match": etag,
        #'If-Modified-Since': modified,
        "User-Agent": ua.random.strip(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    }

    client = httpx.Client()

    try:
        response = client.get(url, headers=headers, timeout=30, follow_redirects=True)

        if response.status_code == 200:
            feed = feedparser.parse(response.text)
            update = True
        elif response.status_code == 304:
            update = False
        else:
            response.raise_for_status()

    except httpx.HTTPStatusError as exc:
        error = f"HTTP status error while requesting {url}: {exc.response.status_code} {exc.response.reason_phrase}"
    except httpx.TimeoutException:
        error = f"Timeout while requesting {url}"
    except Exception as e:
        error = f"Error while requesting {url}: {str(e)}"

    if feed:
        if feed.bozo and not feed.entries:
            logger.warning("Get feed %s %s", url, feed.get("bozo_exception"))
            error = feed.get("bozo_exception")

    return {
        "feed": feed,
        "update": update,
        "error": error,
    }


def fetch_feed(url: str, etag: str = "") -> Dict:
    try:
        ua = UserAgent()
        feed = feedparser.parse(url, etag=etag, agent=ua.random.strip())
        if feed.status == 304:
            logger.info(f"Feed {url} not modified, using cached version.")
            return {
                "feed": None,
                "update": False,
                "error": None,
            }
        if feed.bozo and not feed.entries:
            logger.warning("Manual fetch feed %s %s", url, feed.get("bozo_exception"))
            results = manual_fetch_feed(url, etag)
            return results
        else:
            return {
                "feed": feed,
                "update": True,
                "error": None,
            }
    except Exception as e:
        # logger.warning(f"Failed to fetch feed {url}: {str(e)}")
        return {
            "feed": None,
            "update": False,
            "error": str(e),
        }


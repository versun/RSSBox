import hashlib
import logging
import time
from functools import lru_cache
from urllib.request import BaseHandler
from django.conf import settings
from django.utils import timezone
from core.models import Feed, Entry
from typing import Dict
import feedparser
from fake_useragent import UserAgent
from lxml import etree

logger = logging.getLogger(__name__)

# Plain HTTP-client UA, used as fallback when a WAF blocks browser-like
# User-Agents with a 403 (e.g. Cloudflare's cf-mitigated challenge).
FALLBACK_USER_AGENT = "curl/8.10.1"


class _FeedTimeoutHandler(BaseHandler):
    """Bound feedparser HTTP requests without changing global socket defaults."""

    def http_request(self, request):
        request.timeout = settings.RSS_FETCH_TIMEOUT
        return request

    https_request = http_request


@lru_cache(maxsize=1)
def _user_agent_generator() -> UserAgent:
    # fake-useragent loads its dataset on instantiation; reuse one instance.
    return UserAgent()


def get_fetch_user_agent() -> str:
    """
    Return the User-Agent for feed requests.
    Priority: RSS_FETCH_USER_AGENT env var > random browser UA.
    """
    custom = settings.RSS_FETCH_USER_AGENT.strip()
    if custom:
        return custom
    return _user_agent_generator().random.strip()


def handle_single_feed_fetch(feed: Feed):
    """
    Fetch feeds and update entries with batch processing optimization.
    """
    try:
        feed.fetch_status = None
        etag = (feed.etag or "") if feed.max_posts <= feed.entries.count() else ""
        fetch_results = fetch_feed(url=feed.feed_url, etag=etag)

        if fetch_results["error"]:
            raise Exception(f"Fetch Feed Failed: {fetch_results['error']}")
        elif not fetch_results["update"]:
            feed.fetch_status = True
            feed.log = f"{timezone.now()} Feed is up to date, Skip <br>"
            return

        latest_feed = fetch_results.get("feed")
        _update_feed_metadata(feed, latest_feed)

        # Update entries

        if not getattr(latest_feed, "entries", None):
            return

        _process_feed_entries(feed, latest_feed.entries)

        feed.fetch_status = True
        feed.log = f"{timezone.now()} Fetch Completed <br>"
    except Exception as e:
        logger.error("Task handle_single_feed_fetch %s: %s", feed.feed_url, str(e))
        feed.fetch_status = False
        feed.log = f"{timezone.now()} {str(e)}<br>"
    finally:
        feed.save()


def convert_struct_time_to_datetime(time_str):
    if not time_str:
        return None
    return timezone.datetime.fromtimestamp(
        time.mktime(time_str), tz=timezone.get_default_timezone()
    )


def manual_fetch_feed(url: str, etag: str = "", user_agent: str = "") -> Dict:
    import httpx

    update = False
    feed = {}
    error = None
    response = None
    # If-None-Match is only sent with a real value: feed.etag is nullable in
    # the DB, and httpx rejects a None header value before the request even
    # leaves the process.
    headers = {
        #'If-Modified-Since': modified,
        "User-Agent": user_agent or get_fetch_user_agent(),
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
    if etag:
        headers["If-None-Match"] = etag

    try:
        with httpx.Client() as client:
            response = client.get(
                url,
                headers=headers,
                timeout=settings.RSS_FETCH_TIMEOUT,
                follow_redirects=True,
            )

            # A 403 here usually means a WAF (e.g. Cloudflare) blocked the
            # browser-like UA: retry once with a plain HTTP-client UA and a
            # matching minimal header set, so the client fingerprint is
            # consistent and often passes.
            if (
                response.status_code == 403
                and headers["User-Agent"] != FALLBACK_USER_AGENT
            ):
                logger.warning(
                    "Got 403 for %s, retrying with fallback UA", url
                )
                fallback_headers = {
                    "User-Agent": FALLBACK_USER_AGENT,
                    "Accept": "*/*",
                }
                if etag:
                    fallback_headers["If-None-Match"] = etag
                response = client.get(
                    url,
                    headers=fallback_headers,
                    timeout=settings.RSS_FETCH_TIMEOUT,
                    follow_redirects=True,
                )

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
        agent = get_fetch_user_agent()
        feed = feedparser.parse(
            url, etag=etag, agent=agent, handlers=[_FeedTimeoutHandler()]
        )
        status = feed.get("status")
        if status == 304:
            logger.info(f"Feed {url} not modified, using cached version.")
            return {
                "feed": None,
                "update": False,
                "error": None,
            }
        # Fallback to manual_fetch_feed (httpx) on HTTP error status (>= 400),
        # such as 403 WAF blocks, 404, 500, or when feedparser encountered
        # a bozo exception without parsing any entries (e.g. connection failure).
        if (isinstance(status, int) and status >= 400) or (
            feed.bozo and not feed.entries
        ):
            logger.warning(
                "Manual fetch feed %s status=%s bozo=%s %s",
                url,
                status,
                feed.bozo,
                feed.get("bozo_exception"),
            )
            results = manual_fetch_feed(url, etag, user_agent=agent)
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


def _update_feed_metadata(feed, latest_feed):
    """Update feed metadata"""
    feed_name_is_the_default = (
        feed.name is None or feed.name == "Loading" or feed.name == "Empty"
    )
    feed.name = latest_feed.feed.get("title") if feed_name_is_the_default else feed.name
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


def _serialize_enclosures(entry_data):
    """Serialize feedparser's ``enclosures`` list for Entry.enclosures_xml.

    feedparser exposes attachments as a list of dicts (href/type/length);
    core.cache._add_atom_entry reads them back from
    ``<enclosures><enclosure href=".." type=".." length=".."/></enclosures>``.
    """
    root = etree.Element("enclosures")
    for enclosure in entry_data.get("enclosures") or []:
        href = enclosure.get("href")
        if not href:
            continue
        try:
            element = etree.Element("enclosure", href=href)
            if enclosure.get("type"):
                element.set("type", enclosure["type"])
            # Only a numeric length is valid Atom (and feed2json int()s it).
            if str(enclosure.get("length", "")).isdigit():
                element.set("length", str(enclosure["length"]))
        except ValueError:
            # Control characters from a malformed source are not valid XML;
            # drop that attachment instead of aborting the whole fetch.
            continue
        root.append(element)
    if not len(root):
        return None
    return etree.tostring(root, encoding="unicode")


def _source_hash(content: str) -> str:
    """Fingerprint the RSS payload a publisher served for one item.

    Only the payload goes in: the title is compared verbatim, and
    ``original_content`` cannot be trusted because the translation
    pipeline replaces it with a fetched full article.
    """
    payload = (content or "").encode("utf-8", "surrogatepass")
    return hashlib.sha256(payload).hexdigest()


def _prepare_entry_data(entry_data, feed):
    """Prepare entry data"""
    from core.tasks.utils import extract_content_from_entry

    content = extract_content_from_entry(entry_data)
    guid = entry_data.get("id") or entry_data.get("link")

    if not guid:
        return None

    return {
        "link": entry_data.get("link", ""),
        "author": entry_data.get("author", feed.author),
        "pubdate": convert_struct_time_to_datetime(entry_data.get("published_parsed")),
        "updated": convert_struct_time_to_datetime(entry_data.get("updated_parsed")),
        "original_title": entry_data.get("title", "No title"),
        "original_content": content,
        "original_summary": entry_data.get("summary"),
        "enclosures_xml": _serialize_enclosures(entry_data),
        "guid": guid,
        "source_hash": _source_hash(content),
    }


def _content_changed(feed, entry_values, old_content, old_summary, old_hash) -> bool:
    """Did the publisher change the payload of an item we already store?

    With a fingerprint the answer is exact. Rows written before the column
    existed carry NULL: ``original_content`` may already hold a fetched
    article there, so it cannot be compared with the re-served payload.
    ``original_summary`` is still the publisher's own text, which keeps the
    upgrade pass free of both a mass re-translation and a missed update.
    """
    if old_hash is not None:
        return entry_values["source_hash"] != old_hash
    if feed.fetch_article:
        return entry_values["original_summary"] != old_summary
    return entry_values["original_content"] != old_content


def _process_feed_entries(feed, entries):
    """Create new entries and refresh existing ones when the publisher
    re-serves an item.

    New rows are deduped by guid (batch-local `seen_guids` plus
    `ignore_conflicts`) since the table has no (feed, guid) constraint.
    Existing rows are updated via bulk_update so a bumped ``updated``
    timestamp reaches Filter.needs_re_evaluation and lets stale AI
    decisions get re-evaluated. Derived translations/summaries are only
    cleared for the input that actually changed: a new title stales the
    title translation, a new payload stales the content translation and
    its summary.
    """
    BATCH_SIZE = 50
    entries_to_create = []
    entries_to_update = []
    # Track pk/updated plus the source fields we refresh. ``source_hash``
    # is the change signal: it fingerprints the RSS payload, so a re-fetch
    # compares upstream against upstream even when ``original_content``
    # now holds a fetched full article (issue #181).
    existing_entries = {
        guid: (
            pk,
            updated,
            original_title,
            original_content,
            original_summary,
            source_hash,
        )
        for (
            guid,
            pk,
            updated,
            original_title,
            original_content,
            original_summary,
            source_hash,
        ) in Entry.objects.filter(feed=feed).values_list(
            "guid",
            "pk",
            "updated",
            "original_title",
            "original_content",
            "original_summary",
            "source_hash",
        )
    }

    # Sort entries by publication date (newest first)
    sorted_entries = sorted(
        entries,
        key=lambda x: (
            x.get("published_parsed") or x.get("updated_parsed") or time.gmtime(0)
        ),
        reverse=True,
    )[: feed.max_posts]

    seen_guids = set(existing_entries)

    for entry_data in sorted_entries:
        entry_values = _prepare_entry_data(entry_data, feed)
        if not entry_values:
            continue

        guid = entry_values["guid"]
        if guid not in seen_guids:
            seen_guids.add(guid)
            entries_to_create.append(Entry(feed=feed, **entry_values))
        elif guid in existing_entries:
            (
                pk,
                _old_updated,
                old_title,
                old_content,
                old_summary,
                old_source_hash,
            ) = existing_entries[guid]
            # A publisher that stops serving <updated> should not erase
            # the timestamp we already have: needs_re_evaluation depends
            # on it and None permanently disables re-evaluation.
            refreshed_updated = entry_values["updated"] or _old_updated
            title_changed = entry_values["original_title"] != old_title
            content_changed = _content_changed(
                feed, entry_values, old_content, old_summary, old_source_hash
            )
            refreshed = Entry(
                pk=pk,
                feed=feed,
                guid=guid,
                link=entry_values["link"],
                author=entry_values["author"],
                pubdate=entry_values["pubdate"],
                updated=refreshed_updated,
                original_title=entry_values["original_title"],
                # Only a changed payload may replace what is stored: a
                # fetched full article is richer than the re-served
                # summary and must survive an identical re-fetch.
                original_content=(
                    entry_values["original_content"]
                    if content_changed
                    else old_content
                ),
                original_summary=entry_values["original_summary"],
                enclosures_xml=entry_values["enclosures_xml"],
                source_hash=entry_values["source_hash"],
            )
            # Invalidate per input, never per entry: a title change stales
            # only the title translation, a payload change stales the
            # content translation and the summary built from it.
            cleared = []
            if title_changed:
                cleared.append("translated_title")
            if content_changed:
                cleared.extend(["translated_content", "ai_summary", "article_fetched"])
            for field in cleared:
                setattr(refreshed, field, False if field == "article_fetched" else None)
            entries_to_update.append((refreshed, tuple(cleared)))

        if len(entries_to_create) >= BATCH_SIZE:
            Entry.objects.bulk_create(entries_to_create, ignore_conflicts=True)
            entries_to_create = []

    if entries_to_create:
        Entry.objects.bulk_create(entries_to_create, ignore_conflicts=True)

    source_fields = [
        "link",
        "author",
        "pubdate",
        "updated",
        "original_title",
        "original_content",
        "original_summary",
        "enclosures_xml",
        "source_hash",
    ]
    derived_fields = [
        "translated_title",
        "translated_content",
        "ai_summary",
        "article_fetched",
    ]
    # bulk_update writes every listed field for every row, so entries that
    # keep a paid translation must not travel with the ones being cleared:
    # group them by the exact derived fields they invalidate (none for an
    # identical re-serve).
    updates_by_cleared = {}
    for refreshed, cleared in entries_to_update:
        updates_by_cleared.setdefault(cleared, []).append(refreshed)
    cleared_stage_map = {
        "translated_title": "title",
        "translated_content": "content",
        "ai_summary": "summary",
    }
    for cleared, rows in updates_by_cleared.items():
        fields = source_fields + [f for f in derived_fields if f in cleared]
        Entry.objects.bulk_update(rows, fields=fields)
        stages = [
            cleared_stage_map[field]
            for field in cleared
            if field in cleared_stage_map
        ]
        if stages:
            from core.tasks.utils import reset_entry_task_states

            reset_entry_task_states(
                [row.pk for row in rows],
                stages=stages,
                include_manual_pause=False,
                clear_chunks=True,
            )

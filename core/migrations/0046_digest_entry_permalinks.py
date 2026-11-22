from django.conf import settings
from django.db import migrations

# Every digest entry used to link to "/rss/digest/view/<slug>", which always
# renders the newest digest. Readers that fetch the entry link for full text
# (e.g. eilmeldung's auto scrape) therefore showed the newest digest for
# every entry. Give each existing entry its own page.
OLD_SEGMENT = "/rss/digest/view/"


def add_entry_ids(apps, schema_editor):
    Digest = apps.get_model("core", "Digest")
    Feed = apps.get_model("core", "Feed")
    Entry = apps.get_model("core", "Entry")
    site = settings.SITE_URL.rstrip("/")
    touched = []
    for digest in Digest.objects.exclude(slug__isnull=True).exclude(slug=""):
        # Only the feed that get_digest_feed()/digest_view serve. A link that
        # merely ends the same way can belong to another feed: a subscribed
        # digest of another RSSBox instance that uses the same slug.
        feed_ids = Feed.objects.filter(
            feed_url=f"{site}/rss/digest/{digest.slug}"
        ).values_list("pk", flat=True)
        suffix = f"{OLD_SEGMENT}{digest.slug}"
        entries = [
            entry
            for entry in Entry.objects.filter(feed_id__in=feed_ids).only("pk", "link")
            if entry.link.rstrip("/").endswith(suffix)
        ]
        for entry in entries:
            entry.link = f"{entry.link.rstrip('/')}/{entry.pk}"
        if entries:
            Entry.objects.bulk_update(entries, ["link"])
            touched.append(digest.pk)

    if touched:
        # The cached feed and its ETag/Last-Modified only change with the
        # digest's cache version. Without a bump, readers keep the old links
        # (304) until the next digest is generated.
        from core.models.filter import bump_cache_version

        for pk in touched:
            bump_cache_version("digest", pk)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0045_entry_task_state_chunk_cache_and_agent_chunk_options"),
    ]

    operations = [
        migrations.RunPython(add_entry_ids, migrations.RunPython.noop),
    ]

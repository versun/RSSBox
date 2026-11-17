from django.db import migrations
from django.db.models import Value
from django.db.models.functions import Replace

# Digest entries created before the entry-link fix stored
# "<SITE_URL>/core/digest/<slug>", but no such route exists
# (core.urls is mounted at /rss/), so the link fell through to
# the admin site and sent readers to the login page. Rewrite
# them to the public digest page.
OLD_DIGEST_LINK_SEGMENT = "/core/digest/"
NEW_DIGEST_LINK_SEGMENT = "/rss/digest/view/"


def fix_digest_entry_links(apps, schema_editor):
    Entry = apps.get_model("core", "Entry")
    Entry.objects.filter(link__contains=OLD_DIGEST_LINK_SEGMENT).update(
        link=Replace(
            "link", Value(OLD_DIGEST_LINK_SEGMENT), Value(NEW_DIGEST_LINK_SEGMENT)
        )
    )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0042_entry_source_hash"),
    ]

    operations = [
        migrations.RunPython(fix_digest_entry_links, migrations.RunPython.noop),
    ]

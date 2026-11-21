from django.db import migrations

# Every digest entry used to link to "/rss/digest/view/<slug>", which always
# renders the newest digest. Readers that fetch the entry link for full text
# (e.g. eilmeldung's auto scrape) therefore showed the newest digest for
# every entry. Give each existing entry its own page.
OLD_SEGMENT = "/rss/digest/view/"


def add_entry_ids(apps, schema_editor):
    Digest = apps.get_model("core", "Digest")
    Entry = apps.get_model("core", "Entry")
    for digest in Digest.objects.all():
        suffix = f"{OLD_SEGMENT}{digest.slug}"
        for entry in Entry.objects.filter(link__endswith=suffix):
            entry.link = f"{entry.link}/{entry.pk}"
            entry.save(update_fields=["link"])
        for entry in Entry.objects.filter(link__endswith=suffix + "/"):
            entry.link = f"{entry.link}{entry.pk}"
            entry.save(update_fields=["link"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0045_entry_task_state_chunk_cache_and_agent_chunk_options"),
    ]

    operations = [
        migrations.RunPython(add_entry_ids, migrations.RunPython.noop),
    ]

from django.db import migrations, models


def _dedupe_entry_guids(apps, schema_editor):
    """Keep one row per (feed, guid) before the unique index lands.

    bulk_create() without a constraint could already have written
    duplicates when two updaters raced; keep the lowest id per pair.
    """
    Entry = apps.get_model("core", "Entry")
    table = Entry._meta.db_table
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            f"DELETE FROM {table} WHERE id NOT IN "
            f"(SELECT MIN(id) FROM {table} GROUP BY feed_id, guid)"
        )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0040_feed_translation_started_at"),
    ]

    operations = [
        migrations.RunPython(_dedupe_entry_guids, migrations.RunPython.noop),
        migrations.AddIndex(
            model_name="entry",
            index=models.Index(
                fields=["feed", "-pubdate"], name="entry_feed_pubdate_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="entry",
            index=models.Index(
                fields=["feed", "guid"], name="entry_feed_guid_idx"
            ),
        ),
    ]

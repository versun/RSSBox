from django.db import migrations

# Older versions stored this marker in Entry.ai_summary when summarizing failed.
# Such entries were never retried and the marker leaked into feeds and digests.
LEGACY_FAILED_SUMMARY_PREFIX = "[Summary failed:"


def clear_legacy_summary_failures(apps, schema_editor):
    Entry = apps.get_model("core", "Entry")
    Entry.objects.filter(ai_summary__startswith=LEGACY_FAILED_SUMMARY_PREFIX).update(
        ai_summary=None
    )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0038_alter_tag_slug"),
    ]

    operations = [
        migrations.RunPython(
            clear_legacy_summary_failures, migrations.RunPython.noop
        ),
    ]

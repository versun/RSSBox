from django.db import migrations


def remove_digest_generated_feeds(apps, schema_editor):
    Feed = apps.get_model("core", "Feed")
    Feed.objects.filter(author="RSSBox Digest").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0036_openaiagent_merge_system_prompt"),
    ]

    operations = [
        migrations.RunPython(
            remove_digest_generated_feeds,
            migrations.RunPython.noop,
        ),
        migrations.DeleteModel(
            name="Digest",
        ),
    ]

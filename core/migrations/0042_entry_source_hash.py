from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0041_entry_feed_pubdate_idx"),
    ]

    operations = [
        migrations.AddField(
            model_name="entry",
            name="source_hash",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
    ]

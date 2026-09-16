from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0036_openaiagent_merge_system_prompt"),
    ]

    operations = [
        migrations.AlterField(
            model_name="feed",
            name="feed_url",
            field=models.URLField(max_length=1000, verbose_name="Feed URL"),
        ),
    ]

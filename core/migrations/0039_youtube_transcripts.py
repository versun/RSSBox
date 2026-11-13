from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0038_alter_tag_slug")]

    operations = [
        migrations.AddField(
            model_name="feed",
            name="fetch_youtube_transcript",
            field=models.BooleanField(
                default=False,
                verbose_name="Use YouTube captions",
                help_text="Use captions for YouTube content translation and summaries. Missing captions stay pending.",
            ),
        ),
        migrations.AddField(
            model_name="feed",
            name="youtube_transcript_fallback",
            field=models.BooleanField(
                default=False,
                verbose_name="Enable yt-dlp caption fallback",
                help_text="Try yt-dlp for caption extraction failures, but not IP blocks or missing captions.",
            ),
        ),
        migrations.CreateModel(
            name="YouTubeTranscript",
            fields=[
                (
                    "video_id",
                    models.CharField(max_length=11, primary_key=True, serialize=False),
                ),
                ("result", models.JSONField(default=dict)),
                ("error_code", models.CharField(max_length=32, blank=True, default="")),
                ("attempts", models.PositiveIntegerField(default=0)),
                (
                    "retry_after",
                    models.DateTimeField(null=True, blank=True, db_index=True),
                ),
                ("lease_id", models.UUIDField(null=True, blank=True)),
                ("lease_until", models.DateTimeField(null=True, blank=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
    ]

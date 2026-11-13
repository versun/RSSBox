from django.db import models


class YouTubeTranscript(models.Model):
    """Shared, persistent caption cache and retry state; independent of feed language."""

    video_id = models.CharField(max_length=11, primary_key=True)
    result = models.JSONField(default=dict)
    error_code = models.CharField(max_length=32, blank=True, default="")
    attempts = models.PositiveIntegerField(default=0)
    retry_after = models.DateTimeField(null=True, blank=True, db_index=True)
    lease_id = models.UUIDField(null=True, blank=True)
    lease_until = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

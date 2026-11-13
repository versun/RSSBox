"""Caption preparation shared by summary-only and translation workflows."""

from datetime import timedelta
from uuid import uuid4

from django.db.models import F, Q
from django.utils import timezone

from core.models.youtube_transcript import YouTubeTranscript
from utils.youtube_transcripts import (
    BLOCK_ERRORS,
    CaptionError,
    fetch_transcript,
    render_transcript,
    validate_result,
    video_id,
)


def uses_youtube_transcript(entry, feed):
    return feed.fetch_youtube_transcript and video_id(entry.link) is not None


def processing_content(entry, feed):
    if not uses_youtube_transcript(entry, feed):
        return entry.original_content
    identifier = video_id(entry.link)
    row, _ = YouTubeTranscript.objects.get_or_create(video_id=identifier)
    if row.result:
        return render_transcript(row.result, identifier)
    now = timezone.now()
    if row.retry_after and row.retry_after > now:
        raise CaptionError(row.error_code)
    # An IP block affects other channels too. Keep cooldown across workers/restarts.
    if YouTubeTranscript.objects.filter(
        error_code__in=BLOCK_ERRORS, retry_after__gt=now
    ).exists():
        raise CaptionError("blocked")
    lease = uuid4()
    claimed = (
        YouTubeTranscript.objects.filter(pk=identifier, result={})
        .filter(Q(lease_until__isnull=True) | Q(lease_until__lte=now))
        .filter(Q(retry_after__isnull=True) | Q(retry_after__lte=now))
        .update(
            lease_id=lease,
            lease_until=now + timedelta(minutes=4),
            attempts=F("attempts") + 1,
        )
    )
    if not claimed:
        raise CaptionError("timeout")
    owned = YouTubeTranscript.objects.filter(pk=identifier, lease_id=lease)
    try:
        result = validate_result(
            fetch_transcript(identifier, feed.youtube_transcript_fallback)
        )
    except Exception as exc:
        code = exc.code if isinstance(exc, CaptionError) else "extraction_failed"
        # Missing captions can appear later, especially for new uploads/live streams.
        hours = (
            24
            if code in {"no_captions", "unavailable"}
            else min(24, 2 ** min(row.attempts, 5))
        )
        owned.update(
            error_code=code,
            retry_after=timezone.now() + timedelta(hours=hours),
            lease_id=None,
            lease_until=None,
            updated_at=timezone.now(),
        )
        raise CaptionError(code) from None
    if not owned.update(
        result=result,
        error_code="",
        retry_after=None,
        lease_id=None,
        lease_until=None,
        updated_at=timezone.now(),
    ):
        raise CaptionError("timeout")
    return render_transcript(result, identifier)

"""Bounded caption extraction. No media download, translation, cookies or shell commands."""

import contextlib
import html
import json
import math
import re
import subprocess
import sys
import tempfile
from urllib.parse import parse_qs, urlparse

MAX_BYTES = 4 * 1024 * 1024
FETCH_TIMEOUT = 90
FALLBACK_ERRORS = {"token_required", "invalid_captions", "extraction_failed"}
BLOCK_ERRORS = {"blocked", "rate_limited"}
ERROR_CODES = (
    FALLBACK_ERRORS
    | BLOCK_ERRORS
    | {
        "unavailable",
        "no_captions",
        "timeout",
        "network_error",
        "missing_dependency",
    }
)


class CaptionError(Exception):
    def __init__(self, code, language=None):
        self.language = (
            language
            if isinstance(language, str)
            and re.fullmatch(r"[A-Za-z0-9-]{1,64}", language)
            else None
        )
        self.code = code if code in ERROR_CODES else "extraction_failed"
        super().__init__(f"YouTube captions: {self.code}; entry remains pending")


def video_id(url):
    """Recognize video links, never channel links or lookalike hosts."""
    try:
        parsed = urlparse(url or "")
        if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
            return None
        host = (parsed.hostname or "").lower()
        parts = parsed.path.strip("/").split("/")
        value = None
        if host in {"youtu.be", "www.youtu.be"} and len(parts) == 1:
            value = parts[0]
        elif host in {
            "youtube.com",
            "www.youtube.com",
            "m.youtube.com",
            "music.youtube.com",
        }:
            if parsed.path == "/watch":
                value = parse_qs(parsed.query).get("v", [None])[0]
            elif len(parts) == 2 and parts[0] in {"shorts", "live", "embed"}:
                value = parts[1]
        elif host in {"youtube-nocookie.com", "www.youtube-nocookie.com"}:
            if len(parts) == 2 and parts[0] == "embed":
                value = parts[1]
        return value if value and re.fullmatch(r"[A-Za-z0-9_-]{11}", value) else None
    except (ValueError, TypeError):
        return None


def validate_result(result):
    """Normalize both providers to plain text and finite, nonnegative timestamps."""
    try:
        if result["provider"] not in {"youtube-transcript-api", "yt-dlp"}:
            raise ValueError
        language = result["language"]
        if not isinstance(language, str) or not language or len(language) > 64:
            raise ValueError
        if not isinstance(result["generated"], bool):
            raise ValueError
        segments = []
        for item in result["segments"]:
            if not isinstance(item["text"], str):
                raise ValueError
            text = item["text"].strip()
            start, duration = float(item["start"]), float(item["duration"])
            if not all(math.isfinite(v) and v >= 0 for v in (start, duration)):
                raise ValueError
            if text:
                segments.append({"text": text, "start": start, "duration": duration})
        if not segments:
            raise ValueError
        normalized = dict(
            provider=result["provider"],
            language=language,
            generated=result["generated"],
            segments=segments,
        )
        if len(json.dumps(normalized).encode()) > MAX_BYTES:
            raise ValueError
        return normalized
    except (KeyError, TypeError, ValueError, OverflowError):
        raise CaptionError("invalid_captions") from None


def render_transcript(result, identifier):
    result = validate_result(result)
    kind = "Automatic captions" if result["generated"] else "Manual captions"
    paragraphs = [f"<p>{kind} ({html.escape(result['language'])})</p>"]
    for segment in result["segments"]:
        seconds = int(segment["start"])
        label = f"{seconds // 60}:{seconds % 60:02d}"
        paragraphs.append(
            f'<p><a href="https://www.youtube.com/watch?v={identifier}&amp;t={seconds}">'
            f"{label}</a> {html.escape(segment['text'])}</p>"
        )
    return "\n".join(paragraphs)


def classify_error(exc):
    # Do not send upstream exception text (which can contain signed URLs) to logs.
    from requests.exceptions import RequestException, Timeout

    name = type(exc).__name__
    if name in {"IpBlocked", "RequestBlocked"}:
        return "blocked"
    if name == "PoTokenRequired":
        return "token_required"
    if name in {"TranscriptsDisabled", "NoTranscriptFound"}:
        return "no_captions"
    if name in {"VideoUnavailable", "VideoUnplayable", "AgeRestricted"}:
        return "unavailable"
    if isinstance(exc, (Timeout, TimeoutError)):
        return "timeout"
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if status == 429:
        return "rate_limited"
    if status in {401, 403}:
        return "blocked"
    if status is not None or isinstance(exc, RequestException):
        return "network_error"
    if name == "YouTubeDataUnparsable":
        return "extraction_failed"
    # yt-dlp wraps HTTP failures in DownloadError. Only classify; never log text.
    message = str(exc).lower()
    if "429" in message or "too many requests" in message:
        return "rate_limited"
    if (
        "403" in message
        or "confirm you’re not a bot" in message
        or "confirm you're not a bot" in message
    ):
        return "blocked"
    if "timed out" in message or "timeout" in message:
        return "timeout"
    if name == "YouTubeRequestFailed" or re.search(r"http error 5\d\d", message):
        return "network_error"
    return "extraction_failed"


def preferred_caption_language(captions):
    """Read YouTube's default audio language, including regional language codes."""
    try:
        audio = captions["audioTracks"][captions["defaultAudioTrackIndex"]]
        if audio.get("audioTrackId"):
            return audio["audioTrackId"].split(".")[0]
        index = audio.get("defaultCaptionTrackIndex")
        if index is None:
            index = audio["captionTrackIndices"][0]
        return captions["captionTracks"][index]["languageCode"]
    except (KeyError, TypeError, IndexError, AttributeError):
        return None


def select_primary_track(tracks, language=None):
    if not tracks:
        raise CaptionError("no_captions")
    manual = [track for track in tracks if not track.is_generated]
    candidates = manual or tracks
    if len(candidates) == 1:
        return candidates[0]
    if language:
        matches = [
            track
            for track in candidates
            if track.language_code.split("-")[0] == language.split("-")[0]
        ]
        if matches:
            return min(matches, key=lambda track: track.language_code != language)
    if manual:
        return manual[0]
    # Multiple automatic tracks can be dubbed audio, not the original speech.
    # Let the fallback resolve the source instead of choosing alphabetically.
    raise CaptionError("extraction_failed")


def fetch_primary(identifier):
    from requests import Session
    from youtube_transcript_api import YouTubeTranscriptApi

    class TimedSession(Session):
        caption_language = None

        def request(self, *args, **kwargs):
            kwargs.setdefault("timeout", (10, 30))
            response = super().request(*args, **kwargs)
            # The library exposes tracks but omits default-audio metadata. Observe
            # its existing player response; do not add another metadata request.
            if urlparse(response.url).path == "/youtubei/v1/player" and response.ok:
                try:
                    captions = (
                        response.json()
                        .get("captions", {})
                        .get("playerCaptionsTracklistRenderer", {})
                    )
                    self.caption_language = preferred_caption_language(captions)
                except (ValueError, TypeError, AttributeError):
                    pass
            return response

    with TimedSession() as session:
        tracks = list(YouTubeTranscriptApi(http_client=session).list(identifier))
        # Never request YouTube's machine-translated captions.
        track = select_primary_track(tracks, session.caption_language)
        try:
            transcript = track.fetch()
            return validate_result(
                {
                    "provider": "youtube-transcript-api",
                    "language": track.language_code,
                    "generated": track.is_generated,
                    "segments": transcript.to_raw_data(),
                }
            )
        except CaptionError as exc:
            raise CaptionError(exc.code, track.language_code) from None
        except Exception as exc:
            raise CaptionError(classify_error(exc), track.language_code) from None


def select_subtitle(info, language_hint=None):
    """Prefer manual tracks, then original automatic tracks (not translated copies)."""
    for generated, field in ((False, "subtitles"), (True, "automatic_captions")):
        candidates = []
        for language, formats in (info.get(field) or {}).items():
            if language == "live_chat":
                continue
            for fmt in formats:
                url = fmt.get("url", "")
                if fmt.get("ext") != "json3" or "tlang" in parse_qs(
                    urlparse(url).query
                ):
                    continue
                candidates.append((language, url))
        if candidates:
            source = language_hint or info.get("language")
            if source:
                matches = [
                    item
                    for item in candidates
                    if item[0].split("-")[0] == source.split("-")[0]
                ]
                if matches:
                    candidates = matches
                elif generated:
                    raise CaptionError("extraction_failed")
            elif generated and len({item[0].split("-")[0] for item in candidates}) > 1:
                # "-orig" can describe an automatic dub too. Do not guess.
                raise CaptionError("extraction_failed")
            language, url = min(
                candidates,
                key=lambda item: (
                    item[0] != source,
                    not item[0].endswith("-orig"),
                    item[0],
                ),
            )
            return language, generated, url
    raise CaptionError("no_captions")


def parse_json3(data):
    try:
        events = json.loads(data)["events"]
        return [
            {
                "text": "".join(part.get("utf8", "") for part in event["segs"]),
                "start": event["tStartMs"] / 1000,
                "duration": event.get("dDurationMs", 0) / 1000,
            }
            for event in events
            if "segs" in event
        ]
    except (KeyError, TypeError, ValueError):
        raise CaptionError("invalid_captions") from None


def fetch_fallback(identifier, language_hint=None):
    from yt_dlp import YoutubeDL

    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 0,
        "extractor_retries": 0,
        "cachedir": False,
        "ignore_no_formats_error": True,
    }
    with YoutubeDL(options) as downloader:
        info = downloader.extract_info(
            f"https://www.youtube.com/watch?v={identifier}", download=False
        )
        language, generated, url = select_subtitle(info, language_hint)
        with downloader.urlopen(url) as response:
            data = response.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise CaptionError("invalid_captions")
        return validate_result(
            {
                "provider": "yt-dlp",
                "language": language,
                "generated": generated,
                "segments": parse_json3(data),
            }
        )


def run_provider(identifier, provider, language_hint=None):
    # A subprocess bounds the entire extraction, including upstream library loops.
    # Output goes to a temporary file so communicate() cannot grow memory unbounded.
    with tempfile.TemporaryFile() as output:
        try:
            process = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "utils.youtube_transcripts",
                    provider,
                    identifier,
                ]
                + ([language_hint] if language_hint else []),
                stdout=output,
                stderr=subprocess.DEVNULL,
                timeout=FETCH_TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise CaptionError("timeout") from None
        output.seek(0)
        data = output.read(MAX_BYTES + 1)
    try:
        if process.returncode or len(data) > MAX_BYTES:
            raise ValueError
        result = json.loads(data)
        if "error" in result:
            raise CaptionError(result["error"], result.get("language"))
        return validate_result(result)
    except (ValueError, TypeError):
        raise CaptionError("invalid_captions") from None


def fetch_transcript(identifier, allow_fallback=False):
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", identifier):
        raise CaptionError("unavailable")
    try:
        return run_provider(identifier, "primary")
    except CaptionError as exc:
        if not allow_fallback or exc.code not in FALLBACK_ERRORS:
            raise
        language_hint = exc.language
    return run_provider(identifier, "fallback", language_hint)


def main():
    try:
        provider, identifier = sys.argv[1:3]
        language_hint = sys.argv[3] if len(sys.argv) == 4 else None
        if provider not in {"primary", "fallback"} or not re.fullmatch(
            r"[A-Za-z0-9_-]{11}", identifier
        ):
            raise CaptionError("unavailable")
        with contextlib.redirect_stdout(sys.stderr):
            result = (
                fetch_primary(identifier)
                if provider == "primary"
                else fetch_fallback(identifier, language_hint)
            )
    except CaptionError as exc:
        result = {"error": exc.code, "language": exc.language}
    except ImportError:
        result = {"error": "missing_dependency"}
    except Exception as exc:
        result = {"error": classify_error(exc)}
    print(json.dumps(result))


if __name__ == "__main__":
    main()

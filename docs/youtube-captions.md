# YouTube captions

Add a YouTube channel RSS feed (native YouTube Atom or RSSHub), then enable
**Use YouTube captions** in the feed settings. Enable content translation,
summarization, or both and choose the corresponding AI/translation agent as usual.
Title translation still uses the RSS title. Video links remain the original YouTube links.

RSSBox uses captions as input to content translation and summarization. It keeps the
original RSS description unchanged, including when displaying the original alongside
a translation. Caption text has escaped HTML and timestamp links. Captions are cached
by video ID in the RSSBox database and shared across feeds and target languages.
Manual captions are preferred; otherwise automatic captions are used. When several
tracks exist, RSSBox prefers the default audio language reported by YouTube. If
multiple automatic tracks exist without that metadata, extraction stays pending
or uses the enabled fallback rather than picking an arbitrary dubbed language. Neither
provider requests machine-translated caption tracks. RSSBox performs translation
using the feed's configured engine.

## Optional fallback

**Enable yt-dlp caption fallback** allows one alternative extraction attempt after
`youtube-transcript-api` reports token requirements, malformed/empty captions, or
an extraction failure. Both Python dependencies are included in the installation;
the fallback remains disabled until selected per feed. yt-dlp requests metadata and
one JSON3 subtitle track only. When the primary provider identifies a caption
language before failing, the fallback retains that language. Ambiguous automatic
dub tracks are rejected when neither provider can identify the source language. It does not download audio or video, run STT, or need
FFmpeg. Each provider has a 90-second total timeout, in addition to HTTP timeouts.

Fallback is not attempted for missing captions, unavailable videos, IP blocking,
rate limits, or network timeouts. These can affect both providers. A fallback cannot
guarantee access to captions that require additional YouTube authorization or a PO
token. This feature does not configure browser cookies, token-provider plugins, or
proxy credentials.

## Failures and retries

An entry without usable captions stays pending; RSSBox will not summarize its RSS
description as a substitute. Its feed log records a short error code without signed
caption URLs or upstream error details. Later scheduled or manual processing retries
pending entries, even when the RSS feed itself returns HTTP 304.

Failures retry after 1, 2, 4, 8, 16, then 24 hours. Missing captions and unavailable
videos retry daily because captions can appear after publication. An IP-block or
rate-limit cooldown also pauses new caption requests for other feeds; already cached
captions remain usable. A database lease prevents simultaneous extraction of the same
video; abandoned leases expire after four minutes. Changing the fallback checkbox
respects existing cooldowns.

Changing **Use YouTube captions** in the feed admin clears existing content
translations and summaries so they can be regenerated from the selected source.
It preserves translated titles and original RSS content. Programmatic settings
updates must clear those derived fields themselves when changing content mode.

The cache is persistent and is included in database backups. It is not removed when
a feed is deleted, so re-subscribing can reuse captions. Administrators can explicitly
delete rows from `core_youtubetranscript` to discard old cached captions or reset
retry state. Back up the database before upgrading. Migration `0039` is reversible;
rolling it back discards the caption cache and the two feed settings, while keeping
RSS entries and their existing translations/summaries.

## Tests

Run `python -m django test core.tests.test_youtube_transcripts` with the usual Django
test settings. Tests mock external services: URL recognition, track selection,
fallback decisions, backoff, leases, cache reuse, HTML escaping, and integration with
both translation and summary-only feeds do not require live YouTube or an AI key.

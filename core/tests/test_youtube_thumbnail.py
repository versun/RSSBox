import feedparser
from django.test import SimpleTestCase, TestCase
from feed2json import feed2json

from core.cache import generate_atom_feed
from core.models import Entry, Feed
from core.tasks.fetch_feeds import _process_feed_entries, _serialize_enclosures

YOUTUBE_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">
  <id>yt:channel:UC_test</id>
  <title>Test Channel</title>
  <link rel="alternate" href="https://www.youtube.com/channel/UC_test"/>
  <entry>
    <id>yt:video:dQw4w9WgXcQ</id>
    <yt:videoId>dQw4w9WgXcQ</yt:videoId>
    <title>Sample Video</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=dQw4w9WgXcQ"/>
    <author><name>Test Channel</name></author>
    <published>2026-11-20T16:00:00+00:00</published>
    <updated>2026-11-20T17:00:00+00:00</updated>
    <media:group>
      <media:title>Sample Video</media:title>
      <media:thumbnail url="https://i4.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg" width="480" height="360"/>
      <media:description>Video description</media:description>
    </media:group>
  </entry>
</feed>
"""


class YoutubeThumbnailTests(SimpleTestCase):
    """YouTube media:thumbnail becomes an image enclosure."""

    def test_youtube_entry_gets_16x9_thumbnail(self):
        xml = _serialize_enclosures(
            {
                "yt_videoid": "abc",
                "media_thumbnail": [
                    {"url": "https://i1.ytimg.com/vi/abc/hqdefault.jpg"}
                ],
            }
        )
        self.assertIn('href="https://i1.ytimg.com/vi/abc/mqdefault.jpg"', xml)
        self.assertIn('type="image/jpeg"', xml)

    def test_other_feeds_keep_media_thumbnail_out(self):
        self.assertIsNone(
            _serialize_enclosures(
                {
                    "media_thumbnail": [{"url": "https://example.com/a.jpg"}],
                }
            )
        )

    def test_existing_image_enclosure_wins(self):
        xml = _serialize_enclosures(
            {
                "yt_videoid": "abc",
                "enclosures": [
                    {"href": "https://example.com/i.png", "type": "image/png"}
                ],
                "media_thumbnail": [
                    {"url": "https://i1.ytimg.com/vi/abc/hqdefault.jpg"}
                ],
            }
        )
        self.assertNotIn("ytimg", xml)

    def test_non_image_enclosure_keeps_youtube_thumbnail(self):
        xml = _serialize_enclosures(
            {
                "yt_videoid": "abc",
                "enclosures": [
                    {"href": "https://example.com/a.mp3", "type": "audio/mpeg"}
                ],
                "media_thumbnail": [
                    {"url": "https://i1.ytimg.com/vi/abc/hqdefault.jpg"}
                ],
            }
        )
        self.assertIn('href="https://example.com/a.mp3"', xml)
        self.assertIn('href="https://i1.ytimg.com/vi/abc/mqdefault.jpg"', xml)

    def test_invalid_urls_and_control_chars_are_skipped(self):
        self.assertIsNone(
            _serialize_enclosures(
                {
                    "yt_videoid": "abc",
                    "media_thumbnail": [
                        {"url": "http:javascript:alert(1)"},
                        {"url": "httpx://i1.ytimg.com/vi/abc/hqdefault.jpg"},
                        {"url": "https://i1.ytimg.com/vi/abc/\x00hqdefault.jpg"},
                    ],
                }
            )
        )
        # Falls back to the next valid thumbnail after skipping invalid ones.
        xml = _serialize_enclosures(
            {
                "yt_videoid": "abc",
                "media_thumbnail": [
                    {"url": "https://i1.ytimg.com/vi/abc/\x00hqdefault.jpg"},
                    {"url": "https://i2.ytimg.com/vi/abc/hqdefault.jpg"},
                ],
            }
        )
        self.assertIn('href="https://i2.ytimg.com/vi/abc/mqdefault.jpg"', xml)

    def test_ytimg_rewrite_requires_ytimg_host_and_plain_path(self):
        non_ytimg = _serialize_enclosures(
            {
                "yt_videoid": "abc",
                "media_thumbnail": [
                    {"url": "https://example.com/ytimg.com/hqdefault.jpg"}
                ],
            }
        )
        self.assertIn('href="https://example.com/ytimg.com/hqdefault.jpg"', non_ytimg)

        signed = _serialize_enclosures(
            {
                "yt_videoid": "abc",
                "media_thumbnail": [
                    {"url": "https://i1.ytimg.com/vi/abc/hqdefault.jpg?sqp=sig"}
                ],
            }
        )
        self.assertIn(
            'href="https://i1.ytimg.com/vi/abc/hqdefault.jpg?sqp=sig"', signed
        )


class YoutubeThumbnailRoundTripTests(TestCase):
    def test_youtube_atom_feed_round_trip(self):
        feed = Feed.objects.create(
            feed_url="https://www.youtube.com/feeds/videos.xml?channel_id=UC_test",
            name="Test Channel",
        )
        parsed = feedparser.parse(YOUTUBE_ATOM)
        _process_feed_entries(feed, parsed.entries)

        entry = Entry.objects.get(feed=feed)
        self.assertIn(
            'href="https://i4.ytimg.com/vi/dQw4w9WgXcQ/mqdefault.jpg"',
            entry.enclosures_xml,
        )

        for feed_type in ("o", "t"):
            atom_xml = generate_atom_feed(feed, feed_type)
            output = feedparser.parse(atom_xml)
            self.assertEqual(
                [(x.href, x.type) for x in output.entries[0].enclosures],
                [("https://i4.ytimg.com/vi/dQw4w9WgXcQ/mqdefault.jpg", "image/jpeg")],
            )
            json_feed = feed2json(atom_xml)
            attachments = json_feed["items"][0]["attachments"]
            self.assertEqual(len(attachments), 1)
            self.assertEqual(
                attachments[0]["url"],
                "https://i4.ytimg.com/vi/dQw4w9WgXcQ/mqdefault.jpg",
            )
            self.assertEqual(attachments[0]["mime_type"], "image/jpeg")
            self.assertEqual(attachments[0]["size_in_bytes"], 0)

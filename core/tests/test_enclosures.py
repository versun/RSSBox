import feedparser
from django.test import TestCase
from feed2json import feed2json

from core.cache import generate_atom_feed
from core.models import Entry, Feed
from core.tasks.fetch_feeds import (
    _prepare_entry_data,
    _process_feed_entries,
    _serialize_enclosures,
)

SOURCE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Source</title>
    <link>https://example.com/</link>
    <description>Feed with attachments</description>
    <item>
      <title>Photo post</title>
      <link>https://example.com/photo</link>
      <guid>https://example.com/photo</guid>
      <pubDate>Sat, 14 Nov 2026 10:00:00 GMT</pubDate>
      <description>Only a picture</description>
      <enclosure url="https://example.com/a.jpg?x=1&amp;y=2" type="image/jpeg" length="0"/>
    </item>
    <item>
      <title>Episode</title>
      <link>https://example.com/episode</link>
      <guid>https://example.com/episode</guid>
      <pubDate>Sat, 14 Nov 2026 11:00:00 GMT</pubDate>
      <description>A podcast episode</description>
      <enclosure url="https://example.com/e.mp3" type="audio/mpeg" length="12345"/>
    </item>
    <item>
      <title>Plain post</title>
      <link>https://example.com/plain</link>
      <guid>https://example.com/plain</guid>
      <pubDate>Sat, 14 Nov 2026 12:00:00 GMT</pubDate>
      <description>No attachment</description>
    </item>
  </channel>
</rss>
"""


class EnclosureRoundTripTests(TestCase):
    def setUp(self):
        self.feed = Feed.objects.create(
            feed_url="https://example.com/rss.xml", name="Source"
        )
        self.parsed = feedparser.parse(SOURCE_RSS)

    def test_serialize_enclosures(self):
        photo, episode, plain = self.parsed.entries
        self.assertEqual(
            _serialize_enclosures(episode),
            '<enclosures><enclosure href="https://example.com/e.mp3" '
            'type="audio/mpeg" length="12345"/></enclosures>',
        )
        self.assertIn("a.jpg?x=1&amp;y=2", _serialize_enclosures(photo))
        self.assertIsNone(_serialize_enclosures(plain))
        self.assertIsNone(_serialize_enclosures({"enclosures": [{"type": "x/y"}]}))

    def test_prepare_entry_data_keeps_enclosures(self):
        values = _prepare_entry_data(self.parsed.entries[1], self.feed)
        self.assertIn("https://example.com/e.mp3", values["enclosures_xml"])

    def test_enclosures_survive_fetch_and_generation(self):
        _process_feed_entries(self.feed, self.parsed.entries)

        stored = {
            e.original_title: e.enclosures_xml
            for e in Entry.objects.filter(feed=self.feed)
        }
        self.assertIsNotNone(stored["Photo post"])
        self.assertIsNotNone(stored["Episode"])
        self.assertIsNone(stored["Plain post"])

        for feed_type in ("o", "t"):
            output = feedparser.parse(generate_atom_feed(self.feed, feed_type))
            enclosures = {
                e.title: [(x.href, x.type, x.get("length")) for x in e.enclosures]
                for e in output.entries
            }
            self.assertEqual(
                enclosures["Photo post"],
                [("https://example.com/a.jpg?x=1&y=2", "image/jpeg", "0")],
            )
            self.assertEqual(
                enclosures["Episode"],
                [("https://example.com/e.mp3", "audio/mpeg", "12345")],
            )
            self.assertEqual(enclosures["Plain post"], [])

    def test_enclosure_without_length_or_with_invalid_length_and_feed2json(self):
        """Enclosures without length (or with non-numeric length) must not emit
        length='None' or crash feed2json."""
        rss = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"><channel><title>S</title>
          <item>
            <title>No length</title>
            <guid>g-no-length</guid>
            <enclosure url="https://example.com/a.mp3" type="audio/mpeg"/>
          </item>
          <item>
            <title>Bad length</title>
            <guid>g-bad-length</guid>
            <enclosure url="https://example.com/b.mp3" type="audio/mpeg" length="unknown"/>
          </item>
        </channel></rss>"""
        parsed = feedparser.parse(rss)
        _process_feed_entries(self.feed, parsed.entries)

        atom_xml = generate_atom_feed(self.feed, "o")
        self.assertNotIn('length="None"', atom_xml)
        self.assertNotIn('length="unknown"', atom_xml)

        json_feed = feed2json(atom_xml)
        attachments = {
            item["title"]: item.get("attachments", []) for item in json_feed["items"]
        }
        self.assertEqual(attachments["No length"][0]["size_in_bytes"], 0)
        self.assertEqual(attachments["Bad length"][0]["size_in_bytes"], 0)

    def test_invalid_xml_chars_in_enclosure_are_skipped(self):
        """A control character in a malformed source must not abort the fetch."""
        self.assertIsNone(
            _serialize_enclosures(
                {"enclosures": [{"href": "https://example.com/\x00bad.mp3"}]}
            )
        )

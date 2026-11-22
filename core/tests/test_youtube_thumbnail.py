from django.test import SimpleTestCase

from core.tasks.fetch_feeds import _serialize_enclosures


class YoutubeThumbnailTests(SimpleTestCase):
    """YouTube media:thumbnail becomes an image enclosure."""

    def test_youtube_entry_gets_16x9_thumbnail(self):
        xml = _serialize_enclosures({
            "yt_videoid": "abc",
            "media_thumbnail": [{"url": "https://i1.ytimg.com/vi/abc/hqdefault.jpg"}],
        })
        self.assertIn('href="https://i1.ytimg.com/vi/abc/mqdefault.jpg"', xml)
        self.assertIn('type="image/jpeg"', xml)

    def test_other_feeds_keep_media_thumbnail_out(self):
        self.assertIsNone(_serialize_enclosures({
            "media_thumbnail": [{"url": "https://example.com/a.jpg"}],
        }))

    def test_existing_image_enclosure_wins(self):
        xml = _serialize_enclosures({
            "yt_videoid": "abc",
            "enclosures": [{"href": "https://example.com/i.png", "type": "image/png"}],
            "media_thumbnail": [{"url": "https://i1.ytimg.com/vi/abc/hqdefault.jpg"}],
        })
        self.assertNotIn("ytimg", xml)

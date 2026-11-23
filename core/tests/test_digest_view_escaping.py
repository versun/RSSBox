from django.test import TestCase
from django.utils import timezone

from core.feed_preview import PREVIEW_CSP
from core.models import Digest, Entry, OpenAIAgent, Tag


class DigestViewEscapingTests(TestCase):
    """digest_view renders model output and admin-entered names: escape them."""

    def setUp(self):
        agent = OpenAIAgent.objects.create(name="Agent", api_key="unused")
        self.digest = Digest.objects.create(
            name="Daily digest",
            summarizer=agent,
            target_language="English",
        )

    def _page(self, summary):
        Entry.objects.create(
            feed=self.digest.get_digest_feed(),
            link=f"https://example.com/rss/digest/view/{self.digest.slug}",
            original_title="Daily digest | today",
            ai_summary=summary,
            pubdate=timezone.now(),
        )
        return self.client.get(f"/rss/digest/view/{self.digest.slug}")

    def test_script_and_event_handlers_in_the_summary_are_removed(self):
        response = self._page(
            "## Heading\n\n"
            "<script>alert('script')</script>\n\n"
            '<img src="x" onerror="alert(\'img\')">\n\n'
            "[bad link](javascript:alert('link')) and "
            "<a href=\"javascript:alert('raw')\">raw bad link</a> and "
            "[good link](https://example.com/post)\n"
        )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertNotIn("<script", body)
        self.assertNotIn("alert(", body)
        self.assertNotIn("onerror", body)
        self.assertNotIn("javascript:", body)
        # Ordinary Markdown output survives, and external links open safely.
        self.assertIn("<h2>Heading</h2>", body)
        self.assertIn(
            '<a href="https://example.com/post" target="_blank" rel="noopener noreferrer">good link</a>',
            body,
        )
        self.assertIn("<a>raw bad link</a>", body)

    def test_digest_and_tag_names_are_escaped(self):
        self.digest.name = "<script>alert('name')</script>"
        self.digest.save()
        self.digest.tags.add(Tag.objects.create(name="<b onmouseover=x>tag</b>"))
        response = self._page("Body")
        body = response.content.decode()
        self.assertIn("&lt;script&gt;alert(&#x27;name&#x27;)&lt;/script&gt;", body)
        self.assertNotIn("<script", body)
        self.assertIn("&lt;b onmouseover=x&gt;tag&lt;/b&gt;", body)

    def test_page_sends_the_preview_security_headers(self):
        response = self._page("Body")
        self.assertEqual(response["Content-Security-Policy"], PREVIEW_CSP)
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.assertIn(
            '<meta name="referrer" content="no-referrer">', response.content.decode()
        )

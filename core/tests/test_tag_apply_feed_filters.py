from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from core.cache import merge_feeds_into_one_atom
from core.models import Entry, Feed, Filter, Tag


class TagApplyFeedFiltersTests(TestCase):
    """Tag.apply_feed_filters: build a tag from each feed's filtered output."""

    def setUp(self):
        self.tag = Tag.objects.create(name="Combined")
        now = timezone.now()

        # Feed A keeps only "python" articles, feed B drops "sports" articles.
        self.feed_a = Feed.objects.create(feed_url="https://a.example/rss.xml", name="A")
        self.feed_b = Feed.objects.create(feed_url="https://b.example/rss.xml", name="B")
        for feed in (self.feed_a, self.feed_b):
            feed.tags.add(self.tag)

        include = Filter.objects.create(name="python only", operation=Filter.INCLUDE)
        include.keywords = "python"
        include.save()
        self.feed_a.filters.add(include)

        exclude = Filter.objects.create(name="no sports", operation=Filter.EXCLUDE)
        exclude.keywords = "sports"
        exclude.save()
        self.feed_b.filters.add(exclude)

        def entry(feed, title, minutes):
            Entry.objects.create(
                feed=feed,
                link=f"https://example.com/{feed.name}/{minutes}",
                original_title=title,
                original_content="body",
                pubdate=now - timedelta(minutes=minutes),
            )

        entry(self.feed_a, "A: python release", 1)
        entry(self.feed_a, "A: rust release", 2)
        entry(self.feed_b, "B: sports results", 3)
        entry(self.feed_b, "B: weather", 4)

    def _titles(self, feed_type="t"):
        xml = merge_feeds_into_one_atom(
            self.tag.slug, [self.feed_a, self.feed_b], feed_type=feed_type
        )
        return [t for t in ("A: python release", "A: rust release",
                            "B: sports results", "B: weather") if t in xml]

    def test_default_ignores_feed_filters(self):
        """Unchanged behaviour: the tag merges every entry of its feeds."""
        self.assertFalse(self.tag.apply_feed_filters)
        self.assertEqual(len(self._titles()), 4)

    def test_enabled_uses_each_feeds_filtered_entries(self):
        self.tag.apply_feed_filters = True
        self.tag.save()
        self.assertEqual(self._titles(), ["A: python release", "B: weather"])

    def test_enabled_applies_to_original_output_too(self):
        self.tag.apply_feed_filters = True
        self.tag.save()
        self.assertEqual(self._titles("o"), ["A: python release", "B: weather"])

    def test_tag_filters_still_apply_on_top(self):
        self.tag.apply_feed_filters = True
        self.tag.save()
        no_weather = Filter.objects.create(name="no weather", operation=Filter.EXCLUDE)
        no_weather.keywords = "weather"
        no_weather.save()
        self.tag.filters.add(no_weather)
        self.assertEqual(self._titles(), ["A: python release"])

    def test_toggling_the_option_invalidates_the_tag_cache(self):
        with patch("core.models.filter.bump_cache_version") as bump:
            self.tag.apply_feed_filters = True
            self.tag.save()
        bump.assert_called_once_with("tag", self.tag.pk)

        with patch("core.models.filter.bump_cache_version") as bump:
            self.tag.name = "Renamed"
            self.tag.save()
        bump.assert_not_called()

    def test_clearing_a_feed_filter_invalidates_tags_that_apply_it(self):
        include = self.feed_a.filters.get()
        other_tag = Tag.objects.create(name="Unfiltered")
        self.feed_a.tags.add(other_tag)
        self.tag.apply_feed_filters = True
        self.tag.save()

        with patch("core.models.filter.bump_cache_version") as bump:
            include.clear_ai_filter_cache_results()
        bump.assert_any_call("rss", self.feed_a.pk)
        bump.assert_any_call("tag", self.tag.pk)
        self.assertNotIn(("tag", other_tag.pk), [c.args for c in bump.call_args_list])

    def test_save_with_explicit_pk_does_not_crash(self):
        tag = Tag(pk=99999, name="Explicit PK")
        tag.save()
        self.assertTrue(Tag.objects.filter(pk=99999).exists())

    def test_save_with_update_fields_skips_unrelated_invalidation(self):
        self.tag.apply_feed_filters = True
        self.tag.last_updated = timezone.now()
        with patch("core.models.filter.bump_cache_version") as bump:
            self.tag.save(update_fields=["last_updated"])
        bump.assert_not_called()

    def test_updated_timestamp_uses_newest_filtered_entry(self):
        from datetime import datetime
        from lxml import etree

        no_python = Filter.objects.create(name="no python", operation=Filter.EXCLUDE)
        no_python.keywords = "python"
        no_python.save()
        self.tag.filters.add(no_python)

        xml = merge_feeds_into_one_atom(self.tag.slug, [self.feed_a, self.feed_b])
        root = etree.fromstring(xml.encode("utf-8"))
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        feed_updated = datetime.fromisoformat(root.find("atom:updated", ns).text)
        newest_kept = self.feed_a.entries.get(original_title="A: rust release")
        self.assertEqual(feed_updated, newest_kept.pubdate)

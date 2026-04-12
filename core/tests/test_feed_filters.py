from unittest.mock import Mock, patch

from django.test import TestCase

from core.models import Entry, Feed, Filter


class FeedFiltersServiceTests(TestCase):
    def setUp(self):
        self.feed = Feed.objects.create(feed_url="https://example.com/filter-service.xml")
        self.entry1 = Entry.objects.create(
            feed=self.feed,
            link="https://example.com/entry1",
            original_title="Python Article",
            original_content="Python content",
        )
        self.entry2 = Entry.objects.create(
            feed=self.feed,
            link="https://example.com/entry2",
            original_title="Rust Article",
            original_content="Rust content",
        )

    def test_apply_feed_filters_applies_all_attached_filters(self):
        from core.services.feed.filters import apply_feed_filters

        filter_obj = Filter.objects.create(
            name="Python Only",
            keywords="Python",
            filter_method=Filter.KEYWORD_ONLY,
            operation=Filter.INCLUDE,
        )
        self.feed.filters.add(filter_obj)

        result = apply_feed_filters(self.feed, self.feed.entries.all())

        self.assertIn(self.entry1, result)
        self.assertNotIn(self.entry2, result)

    @patch("core.models.feed.apply_feed_filters")
    def test_feed_filtered_entries_property_delegates_to_service(self, mock_apply):
        queryset = self.feed.entries.all()
        mock_apply.return_value = queryset

        result = self.feed.filtered_entries

        self.assertEqual(result, queryset)
        mock_apply.assert_called_once()
        args = mock_apply.call_args[0]
        self.assertEqual(args[0], self.feed)
        self.assertEqual(list(args[1]), list(self.feed.entries.all()))

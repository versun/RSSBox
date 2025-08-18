from django.test import TestCase
from django.utils import timezone
from django.db import IntegrityError

from ..models import Feed, Entry, Filter, Tag


class FeedModelTest(TestCase):
    def test_feed_creation_and_defaults(self):
        """Test creating Feed instances with minimal and comprehensive data."""
        # Test minimal data and defaults
        feed_url = "https://example.com/rss.xml"
        feed = Feed.objects.create(feed_url=feed_url)

        self.assertEqual(feed.feed_url, feed_url)
        self.assertEqual(str(feed), feed_url)
        self.assertEqual(feed.update_frequency, 30)
        self.assertEqual(feed.max_posts, 20)
        self.assertEqual(feed.fetch_article, False)
        self.assertEqual(feed.translation_display, 0)
        self.assertEqual(feed.translate_title, False)
        self.assertEqual(feed.translate_content, False)
        self.assertEqual(feed.summary, False)
        self.assertEqual(feed.total_tokens, 0)
        self.assertIsNotNone(feed.slug)
        self.assertEqual(len(feed.slug), 32)

        # Test comprehensive data
        now = timezone.now()
        full_feed = Feed.objects.create(
            name="Comprehensive Test Feed",
            feed_url="https://another-example.com/rss.xml",
            link="https://another-example.com",
            author="Test Author",
            language="en-us",
            pubdate=now,
            update_frequency=60,
            max_posts=100,
            fetch_article=True,
            translation_display=1,
            target_language="zh-hans",
            translate_title=True,
            translate_content=True,
            summary=True,
            summary_detail=0.5,
            additional_prompt="Test prompt",
        )

        self.assertEqual(full_feed.name, "Comprehensive Test Feed")
        self.assertEqual(full_feed.author, "Test Author")
        self.assertEqual(full_feed.pubdate, now)
        self.assertEqual(full_feed.update_frequency, 60)
        self.assertEqual(full_feed.max_posts, 100)
        self.assertTrue(full_feed.fetch_article)
        self.assertEqual(full_feed.translation_display, 1)
        self.assertEqual(full_feed.target_language, "zh-hans")
        self.assertTrue(full_feed.translate_title)
        self.assertTrue(full_feed.translate_content)
        self.assertTrue(full_feed.summary)
        self.assertEqual(full_feed.summary_detail, 0.5)
        self.assertEqual(full_feed.additional_prompt, "Test prompt")

    def test_feed_behavior_and_validation(self):
        """Test Feed update frequency, log truncation, and validation."""
        # Test update frequency thresholds
        test_cases = [(3, 5), (7, 15), (25, 30), (45, 60), (500, 1440), (5000, 10080)]
        for input_freq, expected_freq in test_cases:
            feed = Feed.objects.create(
                feed_url=f"https://example.com/feed{input_freq}.xml",
                update_frequency=input_freq,
            )
            self.assertEqual(feed.update_frequency, expected_freq)

        # Test log truncation
        long_log = "A" * 3000
        feed = Feed.objects.create(
            feed_url="https://example.com/log-test.xml", log=long_log
        )
        self.assertLessEqual(len(feed.log.encode("utf-8")), 2048)
        self.assertTrue(feed.log.endswith("A" * 100))

        # Test translation display
        feed = Feed.objects.create(
            feed_url="https://example.com/display-test.xml", translation_display=1
        )
        self.assertEqual(feed.get_translation_display(), "Translation | Original")

    def test_feed_unique_constraint(self):
        """Test Feed unique constraint on feed_url and target_language."""
        Feed.objects.create(
            feed_url="https://example.com/unique-test.xml", target_language="zh-hans"
        )
        with self.assertRaises(IntegrityError):
            Feed.objects.create(
                feed_url="https://example.com/unique-test.xml",
                target_language="zh-hans",
            )

    def test_feed_generic_foreign_key_cleanup(self):
        """Test Feed generic foreign key cleanup."""
        feed = Feed(feed_url="https://example.com/cleanup-test.xml")
        feed.translator_content_type_id = None
        feed.translator_object_id = 1
        feed.save()
        self.assertIsNone(feed.translator_content_type_id)
        self.assertIsNone(feed.translator_object_id)

    def test_feed_relationships_and_filtering(self):
        """Test Feed relationships and filtered entries."""
        feed = Feed.objects.create(feed_url="https://example.com/rel-test.xml")
        tag = Tag.objects.create(name="Test Tag")
        filter_obj = Filter.objects.create(
            name="Test Filter", filter_method=Filter.KEYWORD_ONLY
        )
        filter_obj.keywords = "test"
        filter_obj.save()

        # Test relationships
        feed.tags.add(tag)
        feed.filters.add(filter_obj)
        self.assertIn(tag, feed.tags.all())
        self.assertIn(filter_obj, feed.filters.all())
        self.assertIn(feed, tag.feeds.all())
        self.assertIn(feed, filter_obj.feeds.all())

        # Test filtered entries
        Entry.objects.create(
            feed=feed,
            original_title="This is a test entry",
            link="http://example.com/entry",
        )
        result = feed.filtered_entries
        self.assertIsNotNone(result)

    def test_feed_field_choices_and_validation(self):
        """Test Feed field validators and choices."""
        feed = Feed.objects.create(
            feed_url="https://example.com/choices-test.xml", summary_detail=0.5
        )
        self.assertEqual(feed.summary_detail, 0.5)

        # Test boundary values
        for value in [0.0, 1.0]:
            feed.summary_detail = value
            feed.save()
            self.assertEqual(feed.summary_detail, value)

        # Test translation display choices
        choices = Feed.TRANSLATION_DISPLAY_CHOICES
        expected_choices = [
            (0, "Only Translation"),
            (1, "Translation | Original"),
            (2, "Original | Translation"),
        ]
        for choice in expected_choices:
            self.assertIn(choice, choices)

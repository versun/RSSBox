from django.test import TestCase
from unittest.mock import Mock

from core.models import Feed, Tag


class FeedActionServiceTests(TestCase):
    def setUp(self):
        self.feed = Feed.objects.create(
            name="Action Feed",
            feed_url="https://example.com/action.xml",
            fetch_status=True,
            translation_status=True,
        )
        self.tag = Tag.objects.create(name="Action Tag")

    def test_force_update_feeds_resets_status_and_submits_task(self):
        from core.services.admin import force_update_feeds

        task_manager = Mock()
        update_multiple_feeds_func = Mock()

        force_update_feeds(
            Feed.objects.filter(id=self.feed.id),
            task_manager=task_manager,
            update_multiple_feeds_func=update_multiple_feeds_func,
        )

        self.feed.refresh_from_db()
        self.assertIsNone(self.feed.fetch_status)
        self.assertIsNone(self.feed.translation_status)
        task_manager.submit_task.assert_called_once()
        args = task_manager.submit_task.call_args[0]
        self.assertEqual(args[0], "Force Update Feeds")
        self.assertIs(args[1], update_multiple_feeds_func)
        self.assertEqual(list(args[2]), list(Feed.objects.filter(id=self.feed.id)))

    def test_force_update_tags_updates_timestamp_and_submits_tasks(self):
        from core.services.admin import force_update_tags

        task_manager = Mock()
        cache_tag_func = Mock()

        force_update_tags(
            Tag.objects.filter(id=self.tag.id),
            task_manager=task_manager,
            cache_tag_func=cache_tag_func,
        )

        self.tag.refresh_from_db()
        self.assertIsNotNone(self.tag.last_updated)
        self.assertEqual(task_manager.submit_task.call_count, 2)
        task_manager.submit_task.assert_any_call(
            "Force Update Tags", cache_tag_func, self.tag.slug, "t", "xml"
        )
        task_manager.submit_task.assert_any_call(
            "Force Update Tags", cache_tag_func, self.tag.slug, "t", "json"
        )

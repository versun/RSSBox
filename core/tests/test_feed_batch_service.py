import uuid

from django.test import TestCase

from core.models import Feed, Filter, OpenAIAgent, Tag


class FeedBatchServiceTests(TestCase):
    def setUp(self):
        self.feed = Feed.objects.create(
            name="Batch Feed",
            feed_url="https://example.com/batch.xml",
        )

    def test_apply_batch_updates_boolean_and_scalar_fields(self):
        from core.services.admin.batch import apply_batch_updates

        apply_batch_updates(
            Feed.objects.filter(id=self.feed.id),
            {
                "translate_title": "True",
                "summary": "False",
                "update_frequency": "Change",
                "update_frequency_value": "60",
            },
        )

        self.feed.refresh_from_db()
        self.assertTrue(self.feed.translate_title)
        self.assertFalse(self.feed.summary)
        self.assertEqual(self.feed.update_frequency, 60)

    def test_apply_batch_updates_tags_filters_and_summarizer(self):
        from core.services.admin.batch import apply_batch_updates

        tag = Tag.objects.create(name="Batch Tag")
        filter_obj = Filter.objects.create(name="Batch Filter")
        agent = OpenAIAgent.objects.create(
            name=f"Batch Agent {uuid.uuid4()}",
            api_key="test-key",
            valid=True,
        )

        apply_batch_updates(
            Feed.objects.filter(id=self.feed.id),
            {
                "tags": "Change",
                "tags_value": [str(tag.id)],
                "filter": "Change",
                "filter_value": [str(filter_obj.id)],
                "summarizer": "Change",
                "summarizer_value": str(agent.id),
            },
        )

        self.feed.refresh_from_db()
        self.assertIn(tag, self.feed.tags.all())
        self.assertIn(filter_obj, self.feed.filters.all())
        self.assertEqual(self.feed.summarizer_id, agent.id)

import json
from unittest.mock import patch

from django.test import TestCase, override_settings

from core.management.commands.feed_updater import update_single_feed
from core.models import Feed, Tag


class ExternalAPIBaseTestCase(TestCase):
    def setUp(self):
        self.feed = Feed.objects.create(
            name="Primary Feed",
            feed_url="https://example.com/feed.xml",
        )
        self.tag = Tag.objects.create(name="Tech")
        self.feed.tags.add(self.tag)

    def auth_headers(self, token: str = "secret-token") -> dict[str, str]:
        return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


class ExternalAPIDisabledTests(ExternalAPIBaseTestCase):
    @override_settings(EXTERNAL_API_ENABLED=False, EXTERNAL_API_TOKEN="secret-token")
    def test_api_is_disabled_by_default(self):
        response = self.client.get("/api/v1/feeds")

        self.assertEqual(response.status_code, 404)


class ExternalAPIMisconfiguredTokenTests(ExternalAPIBaseTestCase):
    @override_settings(EXTERNAL_API_ENABLED=True, EXTERNAL_API_TOKEN=None)
    def test_enabled_api_without_token_returns_503(self):
        response = self.client.get("/api/v1/feeds", **self.auth_headers())

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "External API token is not configured.")


@override_settings(EXTERNAL_API_ENABLED=True, EXTERNAL_API_TOKEN="secret-token")
class ExternalAPIAuthTests(ExternalAPIBaseTestCase):
    def test_missing_token_returns_401(self):
        response = self.client.get("/api/v1/feeds")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Unauthorized.")

    def test_wrong_token_returns_401(self):
        response = self.client.get("/api/v1/feeds", **self.auth_headers("wrong-token"))

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Unauthorized.")

    def test_valid_token_returns_success(self):
        response = self.client.get("/api/v1/feeds", **self.auth_headers())

        self.assertEqual(response.status_code, 200)


@override_settings(EXTERNAL_API_ENABLED=True, EXTERNAL_API_TOKEN="secret-token")
class ExternalAPIFeedTests(ExternalAPIBaseTestCase):
    def test_feed_create_rejects_invalid_feed_url(self):
        response = self.client.post(
            "/api/v1/feeds",
            data=json.dumps({"feed_url": "not-a-url"}),
            content_type="application/json",
            **self.auth_headers(),
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["detail"][0]["loc"],
            ["body", "payload", "feed_url"],
        )
        self.assertFalse(Feed.objects.filter(feed_url="not-a-url").exists())

    def test_list_and_detail_only_expose_safe_fields(self):
        list_response = self.client.get("/api/v1/feeds", **self.auth_headers())
        detail_response = self.client.get(
            f"/api/v1/feeds/{self.feed.id}",
            **self.auth_headers(),
        )

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(detail_response.status_code, 200)

        list_payload = list_response.json()
        detail_payload = detail_response.json()

        self.assertEqual([item["id"] for item in list_payload], [self.feed.id])
        self.assertEqual(detail_payload["id"], self.feed.id)
        self.assertEqual(detail_payload["tags"][0]["id"], self.tag.id)

        for payload in [list_payload[0], detail_payload]:
            self.assertNotIn("log", payload)
            self.assertNotIn("etag", payload)
            self.assertNotIn("total_tokens", payload)
            self.assertNotIn("total_characters", payload)
            self.assertNotIn("translator_object_id", payload)
            self.assertNotIn("entries", payload)

    def test_feed_crud(self):
        create_response = self.client.post(
            "/api/v1/feeds",
            data=json.dumps(
                {
                    "feed_url": "https://example.com/new-feed.xml",
                    "name": "Created Feed",
                    "update_frequency": 7,
                    "max_posts": 15,
                    "fetch_article": True,
                    "translate_title": True,
                    "translation_display": 1,
                }
            ),
            content_type="application/json",
            **self.auth_headers(),
        )

        self.assertEqual(create_response.status_code, 201)
        created_payload = create_response.json()
        created_id = created_payload["id"]
        self.assertEqual(created_payload["name"], "Created Feed")
        self.assertEqual(created_payload["update_frequency"], 15)
        self.assertEqual(created_payload["max_posts"], 15)
        self.assertTrue(created_payload["fetch_article"])
        self.assertTrue(created_payload["translate_title"])
        self.assertEqual(created_payload["translation_display"], 1)

        patch_response = self.client.patch(
            f"/api/v1/feeds/{created_id}",
            data=json.dumps(
                {
                    "name": "Updated Feed",
                    "summary": True,
                    "fetch_article": False,
                }
            ),
            content_type="application/json",
            **self.auth_headers(),
        )

        self.assertEqual(patch_response.status_code, 200)
        updated_payload = patch_response.json()
        self.assertEqual(updated_payload["name"], "Updated Feed")
        self.assertTrue(updated_payload["summary"])
        self.assertFalse(updated_payload["fetch_article"])

        delete_response = self.client.delete(
            f"/api/v1/feeds/{created_id}",
            **self.auth_headers(),
        )

        self.assertEqual(delete_response.status_code, 204)
        self.assertFalse(Feed.objects.filter(id=created_id).exists())

    def test_duplicate_feed_create_returns_409(self):
        response = self.client.post(
            "/api/v1/feeds",
            data=json.dumps({"feed_url": self.feed.feed_url}),
            content_type="application/json",
            **self.auth_headers(),
        )

        self.assertEqual(response.status_code, 409)

    def test_duplicate_feed_update_returns_409(self):
        other_feed = Feed.objects.create(
            feed_url="https://example.com/second-feed.xml",
            name="Second Feed",
        )

        response = self.client.patch(
            f"/api/v1/feeds/{other_feed.id}",
            data=json.dumps({"feed_url": self.feed.feed_url}),
            content_type="application/json",
            **self.auth_headers(),
        )

        self.assertEqual(response.status_code, 409)

    def test_feed_create_rejects_update_frequency_above_weekly(self):
        response = self.client.post(
            "/api/v1/feeds",
            data=json.dumps(
                {
                    "feed_url": "https://example.com/too-frequent.xml",
                    "update_frequency": 20000,
                }
            ),
            content_type="application/json",
            **self.auth_headers(),
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["detail"][0]["loc"],
            ["body", "payload", "update_frequency"],
        )
        self.assertFalse(
            Feed.objects.filter(feed_url="https://example.com/too-frequent.xml").exists()
        )

    def test_duplicate_root_feed_url_create_returns_409(self):
        Feed.objects.create(feed_url="https://example.com", name="Root Feed")

        response = self.client.post(
            "/api/v1/feeds",
            data=json.dumps({"feed_url": "https://example.com"}),
            content_type="application/json",
            **self.auth_headers(),
        )

        self.assertEqual(response.status_code, 409)

    def test_feed_create_preserves_root_feed_url_string(self):
        response = self.client.post(
            "/api/v1/feeds",
            data=json.dumps({"feed_url": "https://example.net"}),
            content_type="application/json",
            **self.auth_headers(),
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["feed_url"], "https://example.net")
        self.assertTrue(Feed.objects.filter(feed_url="https://example.net").exists())

    def test_feed_create_rejects_null_target_language(self):
        response = self.client.post(
            "/api/v1/feeds",
            data=json.dumps(
                {
                    "feed_url": "https://example.com/null-target-language.xml",
                    "target_language": None,
                }
            ),
            content_type="application/json",
            **self.auth_headers(),
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["detail"][0]["loc"],
            ["body", "payload", "target_language"],
        )
        self.assertFalse(
            Feed.objects.filter(
                feed_url="https://example.com/null-target-language.xml"
            ).exists()
        )

    def test_feed_update_rejects_null_fetch_article(self):
        response = self.client.patch(
            f"/api/v1/feeds/{self.feed.id}",
            data=json.dumps({"fetch_article": None}),
            content_type="application/json",
            **self.auth_headers(),
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["detail"][0]["loc"],
            ["body", "payload", "fetch_article"],
        )

        self.feed.refresh_from_db()
        self.assertFalse(self.feed.fetch_article)

    def test_feed_update_changing_feed_url_clears_existing_entries(self):
        self.feed.entries.create(
            original_title="Old Entry",
            link="https://example.com/old-entry",
        )

        response = self.client.patch(
            f"/api/v1/feeds/{self.feed.id}",
            data=json.dumps({"feed_url": "https://example.com/new-feed.xml"}),
            content_type="application/json",
            **self.auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.feed.refresh_from_db()
        self.assertEqual(self.feed.feed_url, "https://example.com/new-feed.xml")
        self.assertEqual(self.feed.entries.count(), 0)

    def test_feed_update_changing_target_language_clears_translation_fields(self):
        entry = self.feed.entries.create(
            original_title="Old Entry",
            link="https://example.com/old-entry",
            translated_title="旧标题",
            translated_content="旧内容",
            ai_summary="旧摘要",
        )

        response = self.client.patch(
            f"/api/v1/feeds/{self.feed.id}",
            data=json.dumps({"target_language": "English"}),
            content_type="application/json",
            **self.auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        entry.refresh_from_db()
        self.assertIsNone(entry.translated_title)
        self.assertIsNone(entry.translated_content)
        self.assertIsNone(entry.ai_summary)

    def test_refresh_endpoint_queues_async_update(self):
        with patch("core.api.task_manager.submit_task") as mock_submit_task:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    f"/api/v1/feeds/{self.feed.id}/refresh",
                    data=json.dumps({}),
                    content_type="application/json",
                    **self.auth_headers(),
                )

        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertEqual(payload["status"], "queued")
        mock_submit_task.assert_called_once()
        args = mock_submit_task.call_args.args
        self.assertEqual(args[0], f"update_feed_{self.feed.slug}")
        self.assertIs(args[1], update_single_feed)
        self.assertEqual(args[2].id, self.feed.id)


@override_settings(EXTERNAL_API_ENABLED=True, EXTERNAL_API_TOKEN="secret-token")
class ExternalAPITagTests(ExternalAPIBaseTestCase):
    def test_tag_crud(self):
        list_response = self.client.get("/api/v1/tags", **self.auth_headers())
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual([item["id"] for item in list_response.json()], [self.tag.id])

        create_response = self.client.post(
            "/api/v1/tags",
            data=json.dumps({"name": "Science"}),
            content_type="application/json",
            **self.auth_headers(),
        )

        self.assertEqual(create_response.status_code, 201)
        created_tag_id = create_response.json()["id"]

        patch_response = self.client.patch(
            f"/api/v1/tags/{created_tag_id}",
            data=json.dumps({"name": "Science Daily"}),
            content_type="application/json",
            **self.auth_headers(),
        )

        self.assertEqual(patch_response.status_code, 200)
        self.assertEqual(patch_response.json()["name"], "Science Daily")

        delete_response = self.client.delete(
            f"/api/v1/tags/{created_tag_id}",
            **self.auth_headers(),
        )

        self.assertEqual(delete_response.status_code, 204)
        self.assertFalse(Tag.objects.filter(id=created_tag_id).exists())

    def test_tag_update_rejects_null_name(self):
        response = self.client.patch(
            f"/api/v1/tags/{self.tag.id}",
            data=json.dumps({"name": None}),
            content_type="application/json",
            **self.auth_headers(),
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["detail"][0]["loc"],
            ["body", "payload", "name"],
        )

        self.tag.refresh_from_db()
        self.assertEqual(self.tag.name, "Tech")

    def test_feed_tag_assignment_replaces_and_clears(self):
        second_tag = Tag.objects.create(name="News")

        replace_response = self.client.post(
            f"/api/v1/feeds/{self.feed.id}/tags",
            data=json.dumps({"tag_ids": [second_tag.id]}),
            content_type="application/json",
            **self.auth_headers(),
        )

        self.assertEqual(replace_response.status_code, 200)
        self.feed.refresh_from_db()
        self.assertEqual(
            list(self.feed.tags.values_list("id", flat=True).order_by("id")),
            [second_tag.id],
        )
        self.assertEqual(
            [tag["id"] for tag in replace_response.json()["tags"]],
            [second_tag.id],
        )

        clear_response = self.client.post(
            f"/api/v1/feeds/{self.feed.id}/tags",
            data=json.dumps({"tag_ids": []}),
            content_type="application/json",
            **self.auth_headers(),
        )

        self.assertEqual(clear_response.status_code, 200)
        self.feed.refresh_from_db()
        self.assertEqual(self.feed.tags.count(), 0)
        self.assertEqual(clear_response.json()["tags"], [])

    def test_feed_tag_assignment_rejects_unknown_tag_ids(self):
        response = self.client.post(
            f"/api/v1/feeds/{self.feed.id}/tags",
            data=json.dumps({"tag_ids": [999999]}),
            content_type="application/json",
            **self.auth_headers(),
        )

        self.assertEqual(response.status_code, 404)

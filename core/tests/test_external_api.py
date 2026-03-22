import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from django.utils.http import http_date

from core.management.commands.feed_updater import update_single_feed
from core.models import Digest, Feed, OpenAIAgent, Tag


class ExternalAPIBaseTestCase(TestCase):
    FEED_CACHE_VARIANTS = (
        ("o", "xml"),
        ("o", "json"),
        ("t", "xml"),
        ("t", "json"),
    )
    TAG_CACHE_VARIANTS = (
        ("o", "xml"),
        ("t", "xml"),
        ("t", "json"),
    )

    def setUp(self):
        self.feed = Feed.objects.create(
            name="Primary Feed",
            feed_url="https://example.com/feed.xml",
        )
        self.tag = Tag.objects.create(name="Tech")
        self.feed.tags.add(self.tag)

    def auth_headers(self, token: str = "secret-token") -> dict[str, str]:
        return {"HTTP_AUTHORIZATION": f"Bearer {token}"}

    def feed_cache_keys(self, feed: Feed | None = None) -> list[str]:
        feed = feed or self.feed
        return [
            f"cache_rss_{feed.slug}_{feed_type}_{format_type}"
            for feed_type, format_type in self.FEED_CACHE_VARIANTS
        ]

    def tag_cache_keys(self, tag: Tag | None = None) -> list[str]:
        tag = tag or self.tag
        return [
            f"cache_tag_{tag.slug}_{feed_type}_{format_type}"
            for feed_type, format_type in self.TAG_CACHE_VARIANTS
        ]

    def create_digest_backing_feed(self) -> Feed:
        agent = OpenAIAgent.objects.create(name="Digest Agent", api_key="test-key")
        digest = Digest.objects.create(name="Daily Digest", summarizer=agent)
        digest.tags.add(self.tag)
        return digest.get_digest_feed()

    def create_valid_openai_agent(self, name: str = "External API Agent") -> OpenAIAgent:
        return OpenAIAgent.objects.create(
            name=name,
            api_key="test-key",
            valid=True,
        )

    def translator_option(self, agent: OpenAIAgent) -> str:
        content_type = ContentType.objects.get_for_model(agent.__class__)
        return f"{content_type.id}:{agent.id}"


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

    def test_feed_create_rejects_name_above_model_max_length(self):
        response = self.client.post(
            "/api/v1/feeds",
            data=json.dumps(
                {
                    "feed_url": "https://example.com/too-long-name.xml",
                    "name": "x" * 300,
                }
            ),
            content_type="application/json",
            **self.auth_headers(),
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["detail"][0]["loc"],
            ["body", "payload", "name"],
        )
        self.assertFalse(
            Feed.objects.filter(feed_url="https://example.com/too-long-name.xml").exists()
        )

    def test_feed_update_rejects_name_above_model_max_length(self):
        response = self.client.patch(
            f"/api/v1/feeds/{self.feed.id}",
            data=json.dumps({"name": "x" * 300}),
            content_type="application/json",
            **self.auth_headers(),
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["detail"][0]["loc"],
            ["body", "payload", "name"],
        )
        self.feed.refresh_from_db()
        self.assertEqual(self.feed.name, "Primary Feed")

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

    def test_list_feeds_excludes_digest_backing_feeds(self):
        digest_feed = self.create_digest_backing_feed()

        response = self.client.get("/api/v1/feeds", **self.auth_headers())

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.json()], [self.feed.id])
        self.assertNotIn(digest_feed.id, [item["id"] for item in response.json()])

    def test_feed_detail_returns_404_for_digest_backing_feed(self):
        digest_feed = self.create_digest_backing_feed()

        response = self.client.get(
            f"/api/v1/feeds/{digest_feed.id}",
            **self.auth_headers(),
        )

        self.assertEqual(response.status_code, 404)

    def test_list_feeds_avoids_n_plus_one_tag_queries(self):
        for index in range(3):
            feed = Feed.objects.create(feed_url=f"https://example.com/{index}.xml")
            for tag_index in range(2):
                feed.tags.add(Tag.objects.create(name=f"Tag {index}-{tag_index}"))

        with self.assertNumQueries(2):
            response = self.client.get("/api/v1/feeds", **self.auth_headers())

        self.assertEqual(response.status_code, 200)

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
        self.assertFalse(created_payload["translate_title"])
        self.assertEqual(created_payload["translation_display"], 1)

        patch_response = self.client.patch(
            f"/api/v1/feeds/{created_id}",
            data=json.dumps(
                {
                    "name": "Updated Feed",
                    "fetch_article": False,
                }
            ),
            content_type="application/json",
            **self.auth_headers(),
        )

        self.assertEqual(patch_response.status_code, 200)
        updated_payload = patch_response.json()
        self.assertEqual(updated_payload["name"], "Updated Feed")
        self.assertFalse(updated_payload["summary"])
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

    def test_feed_create_rejects_translation_or_summary_flags_without_required_engines(
        self,
    ):
        unsupported_cases = [
            ("translate_title", "translator"),
            ("translate_content", "translator"),
            ("summary", "summarizer"),
        ]

        for field_name, engine_name in unsupported_cases:
            response = self.client.post(
                "/api/v1/feeds",
                data=json.dumps(
                    {
                        "feed_url": f"https://example.com/{field_name}.xml",
                        field_name: True,
                    }
                ),
                content_type="application/json",
                **self.auth_headers(),
            )

            self.assertEqual(response.status_code, 422)
            self.assertIn(engine_name, response.json()["detail"])

    def test_feed_create_accepts_ai_flags_when_agent_configuration_is_provided(self):
        agent = self.create_valid_openai_agent()

        response = self.client.post(
            "/api/v1/feeds",
            data=json.dumps(
                {
                    "feed_url": "https://example.com/ai-enabled.xml",
                    "translate_title": True,
                    "translate_content": True,
                    "summary": True,
                    "translator_option": self.translator_option(agent),
                    "summarizer_id": agent.id,
                }
            ),
            content_type="application/json",
            **self.auth_headers(),
        )

        self.assertEqual(response.status_code, 201)
        created_feed = Feed.objects.get(feed_url="https://example.com/ai-enabled.xml")
        self.assertTrue(created_feed.translate_title)
        self.assertTrue(created_feed.translate_content)
        self.assertTrue(created_feed.summary)
        self.assertEqual(created_feed.translator_object_id, agent.id)
        self.assertEqual(created_feed.summarizer_id, agent.id)

    def test_feed_update_rejects_translation_or_summary_flags_without_required_engines(
        self,
    ):
        unsupported_cases = [
            ("translate_title", "translator"),
            ("translate_content", "translator"),
            ("summary", "summarizer"),
        ]

        for field_name, engine_name in unsupported_cases:
            response = self.client.patch(
                f"/api/v1/feeds/{self.feed.id}",
                data=json.dumps({field_name: True}),
                content_type="application/json",
                **self.auth_headers(),
            )

            self.assertEqual(response.status_code, 422)
            self.assertIn(engine_name, response.json()["detail"])

    def test_feed_update_rejects_clearing_translator_while_translation_remains_enabled(
        self,
    ):
        agent = self.create_valid_openai_agent()
        content_type = ContentType.objects.get_for_model(agent.__class__)
        self.feed.translate_title = True
        self.feed.translator_content_type = content_type
        self.feed.translator_object_id = agent.id
        self.feed.save()

        response = self.client.patch(
            f"/api/v1/feeds/{self.feed.id}",
            data=json.dumps({"translator_option": None}),
            content_type="application/json",
            **self.auth_headers(),
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("translator", response.json()["detail"])
        self.feed.refresh_from_db()
        self.assertEqual(self.feed.translator_content_type_id, content_type.id)
        self.assertEqual(self.feed.translator_object_id, agent.id)

    def test_feed_update_rejects_clearing_summarizer_while_summary_remains_enabled(
        self,
    ):
        agent = self.create_valid_openai_agent()
        self.feed.summary = True
        self.feed.summarizer = agent
        self.feed.save()

        response = self.client.patch(
            f"/api/v1/feeds/{self.feed.id}",
            data=json.dumps({"summarizer_id": None}),
            content_type="application/json",
            **self.auth_headers(),
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("summarizer", response.json()["detail"])
        self.feed.refresh_from_db()
        self.assertEqual(self.feed.summarizer_id, agent.id)

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

    def test_feed_update_changing_feed_url_clears_stale_metadata_fields(self):
        self.feed.subtitle = "Old subtitle"
        self.feed.link = "https://example.com/old-home"
        self.feed.author = "Old author"
        self.feed.language = "en"
        self.feed.pubdate = timezone.now().replace(microsecond=0)
        self.feed.updated = timezone.now().replace(microsecond=0)
        self.feed.save()

        response = self.client.patch(
            f"/api/v1/feeds/{self.feed.id}",
            data=json.dumps({"feed_url": "https://example.com/new-feed.xml"}),
            content_type="application/json",
            **self.auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIsNone(payload["subtitle"])
        self.assertIsNone(payload["link"])
        self.assertIsNone(payload["author"])
        self.assertIsNone(payload["language"])
        self.assertIsNone(payload["pubdate"])
        self.assertIsNone(payload["updated"])

        self.feed.refresh_from_db()
        self.assertIsNone(self.feed.subtitle)
        self.assertIsNone(self.feed.link)
        self.assertIsNone(self.feed.author)
        self.assertIsNone(self.feed.language)
        self.assertIsNone(self.feed.pubdate)
        self.assertIsNone(self.feed.updated)

    def test_feed_update_changing_feed_url_resets_original_conditional_get_metadata(self):
        previous_fetch = timezone.now().replace(microsecond=0) - timedelta(minutes=5)
        self.feed.last_fetch = previous_fetch
        self.feed.last_translate = previous_fetch
        self.feed.etag = "stale-etag"
        self.feed.entries.create(
            original_title="Old Entry",
            link="https://example.com/old-entry",
        )
        self.feed.save()

        response = self.client.patch(
            f"/api/v1/feeds/{self.feed.id}",
            data=json.dumps({"feed_url": "https://example.com/new-feed.xml"}),
            content_type="application/json",
            **self.auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.feed.refresh_from_db()
        self.assertIsNone(self.feed.last_fetch)
        self.assertIsNone(self.feed.last_translate)
        self.assertIsNone(self.feed.etag)
        with (
            patch("core.views.cache.get", return_value=None),
            patch("core.views.cache_rss", return_value="<feed />"),
        ):
            conditional_response = self.client.get(
                f"/rss/proxy/{self.feed.slug}",
                HTTP_IF_NONE_MATCH="stale-etag",
            )

        self.assertEqual(conditional_response.status_code, 200)

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

    def test_feed_update_changing_translator_clears_existing_translations(self):
        old_agent = self.create_valid_openai_agent("Old Translator")
        new_agent = self.create_valid_openai_agent("New Translator")
        content_type = ContentType.objects.get_for_model(old_agent.__class__)
        previous_translate = timezone.now().replace(microsecond=0) - timedelta(minutes=5)
        self.feed.translate_title = True
        self.feed.translate_content = True
        self.feed.translation_status = True
        self.feed.last_translate = previous_translate
        self.feed.translator_content_type = content_type
        self.feed.translator_object_id = old_agent.id
        self.feed.save()
        entry = self.feed.entries.create(
            original_title="Old Entry",
            link="https://example.com/old-entry",
            translated_title="旧标题",
            translated_content="旧内容",
        )

        response = self.client.patch(
            f"/api/v1/feeds/{self.feed.id}",
            data=json.dumps(
                {"translator_option": self.translator_option(new_agent)}
            ),
            content_type="application/json",
            **self.auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        entry.refresh_from_db()
        self.feed.refresh_from_db()
        self.assertIsNone(entry.translated_title)
        self.assertIsNone(entry.translated_content)
        self.assertIsNone(self.feed.last_translate)
        self.assertIsNone(self.feed.translation_status)
        self.assertEqual(self.feed.translator_object_id, new_agent.id)

    def test_feed_update_changing_summarizer_clears_existing_summaries(self):
        old_agent = self.create_valid_openai_agent("Old Summarizer")
        new_agent = self.create_valid_openai_agent("New Summarizer")
        previous_translate = timezone.now().replace(microsecond=0) - timedelta(minutes=5)
        self.feed.summary = True
        self.feed.translation_status = True
        self.feed.last_translate = previous_translate
        self.feed.summarizer = old_agent
        self.feed.save()
        entry = self.feed.entries.create(
            original_title="Old Entry",
            link="https://example.com/old-entry",
            ai_summary="旧摘要",
        )

        response = self.client.patch(
            f"/api/v1/feeds/{self.feed.id}",
            data=json.dumps({"summarizer_id": new_agent.id}),
            content_type="application/json",
            **self.auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        entry.refresh_from_db()
        self.feed.refresh_from_db()
        self.assertIsNone(entry.ai_summary)
        self.assertIsNone(self.feed.last_translate)
        self.assertIsNone(self.feed.translation_status)
        self.assertEqual(self.feed.summarizer_id, new_agent.id)

    def test_feed_update_changing_fetch_article_clears_existing_content_translations(self):
        agent = self.create_valid_openai_agent("Translator")
        content_type = ContentType.objects.get_for_model(agent.__class__)
        previous_translate = timezone.now().replace(microsecond=0) - timedelta(minutes=5)
        self.feed.translate_title = True
        self.feed.translate_content = True
        self.feed.translation_status = True
        self.feed.last_translate = previous_translate
        self.feed.translator_content_type = content_type
        self.feed.translator_object_id = agent.id
        self.feed.save()
        entry = self.feed.entries.create(
            original_title="Old Entry",
            link="https://example.com/old-entry",
            translated_title="旧标题",
            translated_content="旧内容",
        )

        response = self.client.patch(
            f"/api/v1/feeds/{self.feed.id}",
            data=json.dumps({"fetch_article": True}),
            content_type="application/json",
            **self.auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        entry.refresh_from_db()
        self.feed.refresh_from_db()
        self.assertEqual(entry.translated_title, "旧标题")
        self.assertIsNone(entry.translated_content)
        self.assertTrue(self.feed.fetch_article)
        self.assertIsNone(self.feed.last_translate)
        self.assertIsNone(self.feed.translation_status)

    def test_feed_update_disabling_translated_outputs_resets_translated_conditional_get_metadata(
        self,
    ):
        previous_translate = (
            timezone.now().replace(microsecond=0) - timedelta(minutes=5)
        )
        self.feed.translate_title = True
        self.feed.translation_status = True
        self.feed.last_translate = previous_translate
        self.feed.save()
        self.feed.entries.create(
            original_title="Old Entry",
            link="https://example.com/old-entry",
            translated_title="旧标题",
        )

        response = self.client.patch(
            f"/api/v1/feeds/{self.feed.id}",
            data=json.dumps({"translate_title": False}),
            content_type="application/json",
            **self.auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.feed.refresh_from_db()
        self.assertIsNone(self.feed.last_translate)
        with (
            patch("core.views.cache.get", return_value=None),
            patch("core.views.cache_rss", return_value="<feed />"),
        ):
            conditional_response = self.client.get(
                f"/rss/{self.feed.slug}",
                HTTP_IF_MODIFIED_SINCE=http_date(previous_translate.timestamp()),
            )

        self.assertEqual(conditional_response.status_code, 200)

    def test_feed_update_disabling_translated_outputs_clears_existing_fields(self):
        self.feed.translate_title = True
        self.feed.translate_content = True
        self.feed.summary = True
        self.feed.save()
        entry = self.feed.entries.create(
            original_title="Old Entry",
            link="https://example.com/old-entry",
            translated_title="旧标题",
            translated_content="旧内容",
            ai_summary="旧摘要",
        )

        response = self.client.patch(
            f"/api/v1/feeds/{self.feed.id}",
            data=json.dumps(
                {
                    "translate_title": False,
                    "translate_content": False,
                    "summary": False,
                }
            ),
            content_type="application/json",
            **self.auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        entry.refresh_from_db()
        self.assertIsNone(entry.translated_title)
        self.assertIsNone(entry.translated_content)
        self.assertIsNone(entry.ai_summary)

    def test_feed_patch_keeps_translated_feed_renderable_for_name_changes(self):
        agent = self.create_valid_openai_agent()
        content_type = ContentType.objects.get_for_model(agent.__class__)
        self.feed.translate_title = True
        self.feed.translation_status = True
        self.feed.last_translate = timezone.now().replace(microsecond=0)
        self.feed.translator_content_type = content_type
        self.feed.translator_object_id = agent.id
        self.feed.save()
        self.feed.entries.create(
            original_title="Old Title",
            translated_title="新标题",
            link="https://example.com/old-entry",
        )

        response = self.client.patch(
            f"/api/v1/feeds/{self.feed.id}",
            data=json.dumps({"name": "Renamed Feed"}),
            content_type="application/json",
            **self.auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        rss_response = self.client.get(f"/rss/{self.feed.slug}")
        self.assertEqual(rss_response.status_code, 200)

        rss_content = b"".join(rss_response.streaming_content).decode()
        self.assertIn("<title>Renamed Feed</title>", rss_content)
        self.assertIn("新标题", rss_content)
        self.assertNotIn("<error>No feed data available</error>", rss_content)

    def test_feed_patch_preserves_existing_translated_cache_while_translation_in_progress(
        self,
    ):
        agent = self.create_valid_openai_agent()
        content_type = ContentType.objects.get_for_model(agent.__class__)
        self.feed.translate_title = True
        self.feed.translation_status = None
        self.feed.translator_content_type = content_type
        self.feed.translator_object_id = agent.id
        self.feed.save()

        translated_cache_key = f"cache_rss_{self.feed.slug}_t_xml"
        cache.set(translated_cache_key, "<feed><title>cached-translated</title></feed>", 60)

        response = self.client.patch(
            f"/api/v1/feeds/{self.feed.id}",
            data=json.dumps({"name": "Renamed Feed"}),
            content_type="application/json",
            **self.auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        rss_response = self.client.get(f"/rss/{self.feed.slug}")
        self.assertEqual(rss_response.status_code, 200)

        rss_content = b"".join(rss_response.streaming_content).decode()
        self.assertIn("cached-translated", rss_content)
        self.assertNotIn("<error>No feed data available</error>", rss_content)

    def test_feed_patch_preserves_existing_translated_cache_when_patch_enables_translation(
        self,
    ):
        agent = self.create_valid_openai_agent()
        translated_cache_key = f"cache_rss_{self.feed.slug}_t_xml"
        cache.set(
            translated_cache_key,
            "<feed><title>cached-before-enable</title></feed>",
            60,
        )

        response = self.client.patch(
            f"/api/v1/feeds/{self.feed.id}",
            data=json.dumps(
                {
                    "translate_title": True,
                    "translator_option": self.translator_option(agent),
                }
            ),
            content_type="application/json",
            **self.auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        rss_response = self.client.get(f"/rss/{self.feed.slug}")
        self.assertEqual(rss_response.status_code, 200)

        rss_content = b"".join(rss_response.streaming_content).decode()
        self.assertIn("cached-before-enable", rss_content)
        self.assertNotIn("<error>No feed data available</error>", rss_content)

    def test_feed_patch_clears_stale_translated_cache_when_feed_url_changes(self):
        agent = self.create_valid_openai_agent("Translator")
        content_type = ContentType.objects.get_for_model(agent.__class__)
        self.feed.translate_title = True
        self.feed.translation_status = True
        self.feed.translator_content_type = content_type
        self.feed.translator_object_id = agent.id
        self.feed.save()
        self.feed.entries.create(
            original_title="Old Title",
            translated_title="旧标题",
            link="https://example.com/old-entry",
        )

        translated_cache_key = f"cache_rss_{self.feed.slug}_t_xml"
        cache.set(
            translated_cache_key,
            "<feed><title>stale-feed-url-cache</title></feed>",
            60,
        )

        response = self.client.patch(
            f"/api/v1/feeds/{self.feed.id}",
            data=json.dumps({"feed_url": "https://example.com/new-feed.xml"}),
            content_type="application/json",
            **self.auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(cache.get(translated_cache_key))

    def test_feed_patch_clears_stale_translated_cache_when_translator_changes(self):
        old_agent = self.create_valid_openai_agent("Old Translator")
        new_agent = self.create_valid_openai_agent("New Translator")
        content_type = ContentType.objects.get_for_model(old_agent.__class__)
        self.feed.translate_title = True
        self.feed.translation_status = True
        self.feed.translator_content_type = content_type
        self.feed.translator_object_id = old_agent.id
        self.feed.save()
        self.feed.entries.create(
            original_title="Old Title",
            translated_title="旧标题",
            link="https://example.com/old-entry",
        )

        translated_cache_key = f"cache_rss_{self.feed.slug}_t_xml"
        cache.set(
            translated_cache_key,
            "<feed><title>stale-translator-cache</title></feed>",
            60,
        )

        response = self.client.patch(
            f"/api/v1/feeds/{self.feed.id}",
            data=json.dumps(
                {"translator_option": self.translator_option(new_agent)}
            ),
            content_type="application/json",
            **self.auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(cache.get(translated_cache_key))

    def test_feed_patch_preserves_existing_tag_cache_while_translation_in_progress(
        self,
    ):
        agent = self.create_valid_openai_agent()
        content_type = ContentType.objects.get_for_model(agent.__class__)
        stable_tag = Tag.objects.create(name="stabletag")
        self.feed.tags.add(stable_tag)
        self.feed.translate_title = True
        self.feed.translation_status = None
        self.feed.translator_content_type = content_type
        self.feed.translator_object_id = agent.id
        self.feed.save()
        self.feed.entries.create(
            original_title="Old Title",
            link="https://example.com/old-entry",
        )

        translated_tag_cache_key = f"cache_tag_{stable_tag.slug}_t_xml"
        cache.set(
            translated_tag_cache_key,
            "<feed><title>cached-tag-translated</title></feed>",
            60,
        )

        response = self.client.patch(
            f"/api/v1/feeds/{self.feed.id}",
            data=json.dumps({"name": "Renamed Feed"}),
            content_type="application/json",
            **self.auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        rss_response = self.client.get(f"/rss/tag/{stable_tag.slug}")
        self.assertEqual(rss_response.status_code, 200)

        rss_content = b"".join(rss_response.streaming_content).decode()
        self.assertIn("cached-tag-translated", rss_content)
        self.assertNotIn("<error>No feed data available</error>", rss_content)

    def test_feed_patch_clears_cached_feed_output(self):
        for cache_key in self.feed_cache_keys():
            cache.set(cache_key, f"stale:{cache_key}", 60)

        response = self.client.patch(
            f"/api/v1/feeds/{self.feed.id}",
            data=json.dumps({"max_posts": 5}),
            content_type="application/json",
            **self.auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        for cache_key in self.feed_cache_keys():
            self.assertIsNone(cache.get(cache_key))

    def test_feed_delete_clears_cached_feed_output(self):
        for cache_key in self.feed_cache_keys():
            cache.set(cache_key, f"stale:{cache_key}", 60)

        response = self.client.delete(
            f"/api/v1/feeds/{self.feed.id}",
            **self.auth_headers(),
        )

        self.assertEqual(response.status_code, 204)
        for cache_key in self.feed_cache_keys():
            self.assertIsNone(cache.get(cache_key))

    def test_feed_patch_succeeds_even_if_cache_invalidation_fails(self):
        self.client.raise_request_exception = False
        try:
            with patch(
                "core.api.cache.delete_many",
                side_effect=RuntimeError("redis unavailable"),
            ):
                response = self.client.patch(
                    f"/api/v1/feeds/{self.feed.id}",
                    data=json.dumps({"name": "Updated Feed"}),
                    content_type="application/json",
                    **self.auth_headers(),
                )
        finally:
            self.client.raise_request_exception = True

        self.assertEqual(response.status_code, 200)
        self.feed.refresh_from_db()
        self.assertEqual(self.feed.name, "Updated Feed")

    def test_feed_delete_succeeds_even_if_cache_invalidation_fails(self):
        self.client.raise_request_exception = False
        try:
            with patch(
                "core.api.cache.delete_many",
                side_effect=RuntimeError("redis unavailable"),
            ):
                response = self.client.delete(
                    f"/api/v1/feeds/{self.feed.id}",
                    **self.auth_headers(),
                )
        finally:
            self.client.raise_request_exception = True

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Feed.objects.filter(id=self.feed.id).exists())

    def test_refresh_endpoint_queues_async_update_and_warms_caches(self):
        with (
            patch("core.api.update_single_feed") as mock_update_single_feed,
            patch("core.api.cache_rss", create=True) as mock_cache_rss,
            patch("core.api.cache_tag", create=True) as mock_cache_tag,
            patch("core.api.task_manager.submit_task") as mock_submit_task,
        ):
            mock_submit_task.side_effect = (
                lambda task_name, task_fn, *args: task_fn(*args)
            )
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
        self.assertEqual(args[2], self.feed.id)
        mock_update_single_feed.assert_called_once()
        mock_cache_rss.assert_has_calls(
            [
                ((self.feed.slug,), {"feed_type": "o", "format": "xml"}),
                ((self.feed.slug,), {"feed_type": "o", "format": "json"}),
                ((self.feed.slug,), {"feed_type": "t", "format": "xml"}),
                ((self.feed.slug,), {"feed_type": "t", "format": "json"}),
            ]
        )
        mock_cache_tag.assert_has_calls(
            [
                ((self.tag.slug,), {"feed_type": "o", "format": "xml"}),
                ((self.tag.slug,), {"feed_type": "t", "format": "xml"}),
                ((self.tag.slug,), {"feed_type": "t", "format": "json"}),
            ]
        )

    def test_refresh_callback_does_not_recreate_deleted_feed(self):
        with (
            patch("core.api.cache_rss"),
            patch("core.api.cache_tag"),
            patch("core.api.update_single_feed") as mock_update_single_feed,
            patch("core.api.task_manager.submit_task") as mock_submit_task,
        ):
            mock_update_single_feed.side_effect = (
                lambda feed: feed.save() if isinstance(feed, Feed) else None
            )
            mock_submit_task.side_effect = (
                lambda task_name, task_fn, *args: task_fn(*args)
            )
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                refresh_response = self.client.post(
                    f"/api/v1/feeds/{self.feed.id}/refresh",
                    data=json.dumps({}),
                    content_type="application/json",
                    **self.auth_headers(),
                )

            self.assertEqual(refresh_response.status_code, 202)
            delete_response = self.client.delete(
                f"/api/v1/feeds/{self.feed.id}",
                **self.auth_headers(),
            )
            self.assertEqual(delete_response.status_code, 204)

            for callback in callbacks:
                callback()

        self.assertFalse(Feed.objects.filter(id=self.feed.id).exists())


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

    def test_tag_create_rejects_name_above_model_max_length(self):
        response = self.client.post(
            "/api/v1/tags",
            data=json.dumps({"name": "y" * 300}),
            content_type="application/json",
            **self.auth_headers(),
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["detail"][0]["loc"],
            ["body", "payload", "name"],
        )
        self.assertFalse(Tag.objects.filter(name="y" * 300).exists())

    def test_tag_create_rejects_blank_name(self):
        response = self.client.post(
            "/api/v1/tags",
            data=json.dumps({"name": "   "}),
            content_type="application/json",
            **self.auth_headers(),
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["detail"][0]["loc"],
            ["body", "payload", "name"],
        )
        self.assertFalse(Tag.objects.filter(name="").exists())

    def test_tag_update_rejects_name_above_model_max_length(self):
        response = self.client.patch(
            f"/api/v1/tags/{self.tag.id}",
            data=json.dumps({"name": "y" * 300}),
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

    def test_tag_update_rejects_blank_name(self):
        response = self.client.patch(
            f"/api/v1/tags/{self.tag.id}",
            data=json.dumps({"name": "   "}),
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

    def test_feed_tag_assignment_clears_related_tag_caches(self):
        second_tag = Tag.objects.create(name="News")
        for cache_key in self.tag_cache_keys():
            cache.set(cache_key, f"stale:{cache_key}", 60)
        for cache_key in self.tag_cache_keys(second_tag):
            cache.set(cache_key, f"stale:{cache_key}", 60)

        response = self.client.post(
            f"/api/v1/feeds/{self.feed.id}/tags",
            data=json.dumps({"tag_ids": [second_tag.id]}),
            content_type="application/json",
            **self.auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        for cache_key in self.tag_cache_keys():
            self.assertIsNone(cache.get(cache_key))
        for cache_key in self.tag_cache_keys(second_tag):
            self.assertIsNone(cache.get(cache_key))

    def test_feed_tag_assignment_succeeds_even_if_cache_invalidation_fails(self):
        second_tag = Tag.objects.create(name="News")
        self.client.raise_request_exception = False
        try:
            with patch(
                "core.api.cache.delete_many",
                side_effect=RuntimeError("redis unavailable"),
            ):
                response = self.client.post(
                    f"/api/v1/feeds/{self.feed.id}/tags",
                    data=json.dumps({"tag_ids": [second_tag.id]}),
                    content_type="application/json",
                    **self.auth_headers(),
                )
        finally:
            self.client.raise_request_exception = True

        self.assertEqual(response.status_code, 200)
        self.feed.refresh_from_db()
        self.assertEqual(
            list(self.feed.tags.values_list("id", flat=True).order_by("id")),
            [second_tag.id],
        )

    def test_feed_tag_assignment_rejects_unknown_tag_ids(self):
        response = self.client.post(
            f"/api/v1/feeds/{self.feed.id}/tags",
            data=json.dumps({"tag_ids": [999999]}),
            content_type="application/json",
            **self.auth_headers(),
        )

        self.assertEqual(response.status_code, 404)

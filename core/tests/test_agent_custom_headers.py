import json
import logging
from unittest.mock import MagicMock, patch

import h11
import httpx
from bs4 import BeautifulSoup
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.urls import reverse
from openai import OpenAI

from core.forms.agent_form import CustomHeadersWidget, OpenAIAgentForm
from core.models.agent import OpenAIAgent
from utils.http_headers import (
    MAX_CUSTOM_HEADERS,
    MAX_HEADER_NAME_LENGTH,
    MAX_HEADER_VALUE_LENGTH,
    merge_headers,
    parse_and_validate_headers,
    serialize_headers,
)


class HttpHeadersValidationTest(TestCase):
    def test_empty_values_return_empty_dict(self):
        for raw in (None, "", "   ", "{}", {}):
            self.assertEqual(parse_and_validate_headers(raw), {})
        self.assertEqual(serialize_headers({}), "")
        self.assertEqual(serialize_headers(None), "")

    def test_valid_headers_from_json_and_dict(self):
        raw_json = json.dumps(
            {
                "CF-Access-Client-Id": "client-id-123",
                "CF-Access-Client-Secret": "super-secret",
                "cf-aig-authorization": "Bearer gateway-token",
                "Helicone-Auth": "Bearer helicone-key",
                "User-Agent": "MyCustomClient/1.0",
                "HTTP-Referer": "https://example.com",
                "X-Title": "MyRSS",
                "X-With-Tab": "a\tb",
            }
        )
        parsed = parse_and_validate_headers(raw_json)
        self.assertEqual(parsed["CF-Access-Client-Id"], "client-id-123")
        self.assertEqual(parsed["X-With-Tab"], "a\tb")

        parsed_from_dict = parse_and_validate_headers({"X-Custom": "value"})
        self.assertEqual(parsed_from_dict, {"X-Custom": "value"})

    def test_invalid_json_and_non_object_rejected(self):
        for bad in (
            "{bad json",
            "[]",
            '["X-Foo"]',
            '"just a string"',
            "123",
            "true",
            "null",
            ["X-Foo"],
            123,
        ):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                parse_and_validate_headers(bad)

    def test_non_string_values_rejected(self):
        for bad_value in (123, True, False, None, ["a"], {"nested": "v"}):
            with self.subTest(bad_value=bad_value), self.assertRaises(ValidationError):
                parse_and_validate_headers(json.dumps({"X-Test": bad_value}))

    def test_invalid_header_names_rejected(self):
        for bad_name in (
            "",
            "   ",
            " X-Foo",
            "X-Foo ",
            "X Bad",
            "X:Bad",
            "X-Bad\r\n",
            "X-中文",
            "X-Name(1)",
            "A" * (MAX_HEADER_NAME_LENGTH + 1),
        ):
            with self.subTest(bad_name=bad_name), self.assertRaises(ValidationError):
                parse_and_validate_headers({bad_name: "value"})

    def test_non_string_mapping_keys_rejected(self):
        with self.assertRaises(ValidationError):
            parse_and_validate_headers({123: "v"})

    def test_duplicate_keys_rejected_exact_and_case_insensitive(self):
        with self.assertRaises(ValidationError):
            parse_and_validate_headers('{"X-Foo": "1", "X-Foo": "2"}')
        with self.assertRaises(ValidationError):
            parse_and_validate_headers('{"X-Foo": "1", "x-foo": "2"}')
        with self.assertRaises(ValidationError):
            parse_and_validate_headers({"X-Foo": "1", "x-foo": "2"})

    def test_forbidden_headers_including_authorization_rejected(self):
        for auth_variant in ("Authorization", "authorization", "AUTHORIZATION"):
            with self.subTest(header=auth_variant):
                with self.assertRaises(ValidationError) as ctx:
                    parse_and_validate_headers({auth_variant: "Bearer secret"})
                self.assertIn("API Key", " ".join(ctx.exception.messages))

        for reserved in (
            "Host",
            "Content-Length",
            "Transfer-Encoding",
            "Connection",
            "Content-Type",
            "content-type",
        ):
            with self.subTest(header=reserved), self.assertRaises(ValidationError):
                parse_and_validate_headers({reserved: "value"})

    def test_control_chars_crlf_and_non_ascii_values_rejected(self):
        for bad_val in (
            "foo\r\nInjected: bar",
            "foo\nbar",
            "foo\rbar",
            "foo\x00bar",
            "foo\x07bar",
            "foo\x7fbar",
            "café",
            "\x80",
            "\x9f",
            "中文值",
            "emoji-🚀",
            "v" * (MAX_HEADER_VALUE_LENGTH + 1),
        ):
            with (
                self.subTest(bad_val=repr(bad_val[:20])),
                self.assertRaises(ValidationError) as ctx,
            ):
                parse_and_validate_headers({"X-Test": bad_val})
            combined = " ".join(ctx.exception.messages)
            self.assertNotIn(bad_val, combined)

    def test_leading_trailing_whitespace_rejected_and_internal_ows_allowed(self):
        for bad_val in (
            " secret-tok",
            "secret-tok ",
            "\tsecret-tok",
            "secret-tok\t",
            "   ",
        ):
            with (
                self.subTest(bad_val=repr(bad_val)),
                self.assertRaises(ValidationError) as ctx,
            ):
                parse_and_validate_headers({"X-Secret": bad_val})
            combined = " ".join(ctx.exception.messages)
            self.assertNotIn("secret-tok", combined)

        parsed = parse_and_validate_headers(
            {
                "X-Empty": "",
                "X-Space": "foo bar",
                "X-Tab": "foo\tbar",
            }
        )
        self.assertEqual(
            parsed,
            {
                "X-Empty": "",
                "X-Space": "foo bar",
                "X-Tab": "foo\tbar",
            },
        )

    def test_max_custom_headers_limit(self):
        max_ok = {f"X-Header-{i}": f"val-{i}" for i in range(MAX_CUSTOM_HEADERS)}
        self.assertEqual(len(parse_and_validate_headers(max_ok)), MAX_CUSTOM_HEADERS)

        too_many = {f"X-Header-{i}": f"val-{i}" for i in range(MAX_CUSTOM_HEADERS + 1)}
        with self.assertRaises(ValidationError):
            parse_and_validate_headers(too_many)

    def test_merge_headers_case_insensitive_override(self):
        defaults = {
            "HTTP-Referer": "https://www.rssbox.app",
            "X-Title": "RSSBox",
        }
        overrides = {
            "x-title": "CustomApp",
            "CF-Access-Client-Id": "id-1",
        }
        merged = merge_headers(defaults, overrides)
        self.assertEqual(
            merged,
            {
                "HTTP-Referer": "https://www.rssbox.app",
                "x-title": "CustomApp",
                "CF-Access-Client-Id": "id-1",
            },
        )
        self.assertNotIn("X-Title", merged)


class OpenAIAgentCustomHeadersModelTest(TestCase):
    def setUp(self):
        self.agent = OpenAIAgent.objects.create(
            name="Header Test Agent",
            api_key="test-api-key",
            model="gpt-test",
            max_tokens=4096,
            valid=True,
        )

    def test_custom_headers_encrypted_at_rest(self):
        secret_value = "cf-secret-token-987654321"
        payload = json.dumps(
            {
                "CF-Access-Client-Id": "client-abc",
                "CF-Access-Client-Secret": secret_value,
            }
        )
        self.agent.custom_headers = payload
        self.agent.full_clean()
        self.agent.save()

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT custom_headers FROM core_openaiagent WHERE id = %s",
                [self.agent.pk],
            )
            raw_db_value = cursor.fetchone()[0]

        self.assertIsInstance(raw_db_value, str)
        self.assertNotEqual(raw_db_value, "")
        self.assertNotIn(secret_value, raw_db_value)
        self.assertNotIn("CF-Access-Client-Secret", raw_db_value)

        reloaded = OpenAIAgent.objects.get(pk=self.agent.pk)
        self.assertEqual(
            reloaded.get_custom_headers(),
            {
                "CF-Access-Client-Id": "client-abc",
                "CF-Access-Client-Secret": secret_value,
            },
        )

    def test_clean_normalizes_custom_headers_and_rejects_invalid(self):
        self.agent.custom_headers = "{}"
        self.agent.clean()
        self.assertEqual(self.agent.custom_headers, "")

        self.agent.custom_headers = '{\n  "X-Env": "prod"\n}'
        self.agent.clean()
        self.assertEqual(self.agent.custom_headers, '{"X-Env": "prod"}')

        self.agent.custom_headers = json.dumps({"Authorization": "Bearer nope"})
        with self.assertRaises(ValidationError) as ctx:
            self.agent.clean()
        self.assertIn("custom_headers", ctx.exception.message_dict)

    def test_clean_rejects_extra_headers_in_advanced_params(self):
        self.agent.advanced_params = {
            "temperature": 0.2,
            "extra_headers": {"X-Bypass": "1"},
        }
        with self.assertRaises(ValidationError) as ctx:
            self.agent.clean()
        self.assertIn("advanced_params", ctx.exception.message_dict)

    def test_get_custom_headers_ignores_corrupted_value_without_leaking_secret(self):
        prev_disable = logging.root.manager.disable
        logging.disable(logging.NOTSET)
        self.addCleanup(logging.disable, prev_disable)

        self.agent.custom_headers = '{"Authorization": "Bearer ultra-secret-leak"}'
        with self.assertLogs("core.models.agent", level="WARNING") as logs:
            result = self.agent.get_custom_headers()
        self.assertEqual(result, {})
        combined_logs = "\n".join(logs.output)
        self.assertNotIn("ultra-secret-leak", combined_logs)

    def test_chunk_cache_key_excludes_custom_headers(self):
        ctx = {"entry_id": 42, "stage": "content"}
        key_without = self.agent._build_chunk_cache_key("hello", "sys", ctx)

        self.agent.custom_headers = json.dumps({"CF-Access-Client-Secret": "tok-1"})
        key_with_1 = self.agent._build_chunk_cache_key("hello", "sys", ctx)

        self.agent.custom_headers = json.dumps({"CF-Access-Client-Secret": "tok-2"})
        key_with_2 = self.agent._build_chunk_cache_key("hello", "sys", ctx)

        self.assertEqual(key_without, key_with_1)
        self.assertEqual(key_with_1, key_with_2)


class OpenAIAgentCustomHeadersInjectionTest(TestCase):
    def setUp(self):
        self.agent = OpenAIAgent.objects.create(
            name="Injection Agent",
            api_key="test-api-key",
            model="gpt-test",
            max_tokens=4096,
            custom_headers=json.dumps(
                {
                    "CF-Access-Client-Id": "id-123",
                    "x-title": "MyCustomRSSBox",
                }
            ),
        )
        self.expected_headers = {
            "HTTP-Referer": "https://www.rssbox.app",
            "CF-Access-Client-Id": "id-123",
            "x-title": "MyCustomRSSBox",
        }

    @patch("core.models.agent.OpenAI")
    def test_init_passes_merged_default_headers(self, mock_openai):
        self.agent._init()
        mock_openai.assert_called_once()
        self.assertEqual(
            mock_openai.call_args.kwargs["default_headers"],
            self.expected_headers,
        )

    @patch("core.models.agent.OpenAI")
    def test_validate_injects_default_headers_and_omits_extra_headers(
        self, mock_openai
    ):
        mock_client = MagicMock()
        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock(finish_reason="stop")]
        mock_client.with_options.return_value.chat.completions.create.return_value = (
            mock_completion
        )
        mock_openai.return_value = mock_client

        result = self.agent.validate()

        self.assertTrue(result)
        self.assertEqual(
            mock_openai.call_args.kwargs["default_headers"],
            self.expected_headers,
        )
        create_kwargs = (
            mock_client.with_options.return_value.chat.completions.create.call_args.kwargs
        )
        self.assertNotIn("extra_headers", create_kwargs)

    @patch("core.models.agent.OpenAI")
    def test_detect_model_limit_injects_default_headers(self, mock_openai):
        self.agent.max_tokens = 0
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("network stop")
        mock_openai.return_value = mock_client

        limit = self.agent.detect_model_limit(force=True)

        self.assertEqual(limit, 4096)
        self.assertEqual(
            mock_openai.call_args.kwargs["default_headers"],
            self.expected_headers,
        )
        create_kwargs = mock_client.chat.completions.create.call_args.kwargs
        self.assertNotIn("extra_headers", create_kwargs)

    @patch("core.models.agent.get_token_count", return_value=10)
    @patch("core.models.agent.OpenAI")
    def test_completions_injects_default_headers_and_strips_legacy_extra_headers(
        self, mock_openai, _mock_token_count
    ):
        mock_client = MagicMock()
        mock_completion = MagicMock()
        mock_completion.choices = [
            MagicMock(message=MagicMock(content="Translated"), finish_reason="stop")
        ]
        mock_completion.usage = MagicMock(total_tokens=20)
        mock_client.with_options.return_value.chat.completions.create.return_value = (
            mock_completion
        )
        mock_openai.return_value = mock_client

        # Simulate pre-existing legacy row with extra_headers inside advanced_params
        self.agent.advanced_params = {
            "temperature": 0.3,
            "extra_headers": {"Authorization": "Bearer should-be-dropped"},
        }

        prev_disable = logging.root.manager.disable
        logging.disable(logging.NOTSET)
        self.addCleanup(logging.disable, prev_disable)

        with self.assertLogs("core.models.agent", level="WARNING"):
            output = self.agent.completions("hello", system_prompt="sys")

        self.assertEqual(output["text"], "Translated")
        self.assertEqual(
            mock_openai.call_args.kwargs["default_headers"],
            self.expected_headers,
        )
        create_kwargs = (
            mock_client.with_options.return_value.chat.completions.create.call_args.kwargs
        )
        self.assertNotIn("extra_headers", create_kwargs)
        self.assertEqual(create_kwargs["temperature"], 0.3)

    def test_real_openai_sdk_and_h11_wire_headers_with_options(self):
        self.agent.custom_headers = json.dumps(
            {
                "CF-Access-Client-Id": "id-123",
                "x-title": "MyCustomRSSBox",
                "X-Empty": "",
                "X-Internal-OWS": "foo\tbar baz",
            }
        )
        self.agent.save(update_fields=["custom_headers"])

        captured_requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            # Validate outgoing headers pass strict HTTP/1.1 framing rules in h11.
            h11.Request(
                method=request.method,
                target=request.url.raw_path,
                headers=request.headers.raw,
            )
            captured_requests.append(request)
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "created": 1,
                    "model": self.agent.model,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "1"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 5,
                        "completion_tokens": 1,
                        "total_tokens": 6,
                    },
                },
            )

        real_openai = OpenAI
        transport = httpx.MockTransport(handler)
        with (
            httpx.Client(transport=transport) as http_client,
            patch(
                "core.models.agent.OpenAI",
                side_effect=lambda **kwargs: real_openai(
                    http_client=http_client, **kwargs
                ),
            ),
        ):
            client = self.agent._init()
            client.chat.completions.create(
                model=self.agent.model,
                messages=[{"role": "user", "content": "hello"}],
            )
            client.with_options(max_retries=1).chat.completions.create(
                model=self.agent.model,
                messages=[{"role": "user", "content": "hello again"}],
            )

        self.assertEqual(len(captured_requests), 2)
        for req in captured_requests:
            self.assertEqual(
                req.headers["authorization"], f"Bearer {self.agent.api_key}"
            )
            self.assertEqual(req.headers["http-referer"], "https://www.rssbox.app")
            self.assertEqual(req.headers.get_list("x-title"), ["MyCustomRSSBox"])
            self.assertEqual(req.headers["cf-access-client-id"], "id-123")
            self.assertEqual(req.headers["x-empty"], "")
            self.assertEqual(req.headers["x-internal-ows"], "foo\tbar baz")


class OpenAIAgentAdminCustomHeadersTest(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="admin_headers_test",
            email="admin@example.com",
            password="testpassword123",
        )
        self.client.force_login(self.user)
        self.agent = OpenAIAgent.objects.create(
            name="Admin Header Agent",
            api_key="test-api-key",
            model="gpt-test",
            max_tokens=4096,
        )

    def _base_post_data(self, **overrides):
        data = {
            "name": self.agent.name,
            "api_key": "test-api-key",
            "base_url": self.agent.base_url,
            "model": self.agent.model,
            "title_translate_prompt": self.agent.title_translate_prompt,
            "content_translate_prompt": self.agent.content_translate_prompt,
            "summary_prompt": self.agent.summary_prompt,
            "advanced_params": json.dumps({"temperature": 0.2}),
            "custom_headers": "",
            "rate_limit_rpm": 0,
            "max_tokens": 4096,
            "max_chunk_tokens": 4000,
            "request_timeout": "",
            "merge_system_prompt": "",
        }
        data.update(overrides)
        return data

    def _assert_advanced_tab_has_error(self, response, expected_text: str):
        soup = BeautifulSoup(response.content, "html.parser")
        tab_labels = [li.get_text(strip=True) for li in soup.select(".tab-nav li")]
        tab_panes = soup.select(".tab-content .tab-pane")
        self.assertEqual(len(tab_labels), len(tab_panes))
        advanced_idx = tab_labels.index("Advanced")
        advanced_pane = tab_panes[advanced_idx]
        error_lists = advanced_pane.select(".errorlist")
        self.assertTrue(error_lists)
        combined_errors = " ".join(el.get_text(" ", strip=True) for el in error_lists)
        self.assertIn(expected_text, combined_errors)

    def test_form_uses_lightweight_kv_widget_and_validates_via_model_clean(self):
        form = OpenAIAgentForm(instance=self.agent)
        self.assertIsInstance(form.fields["custom_headers"].widget, CustomHeadersWidget)
        rendered = str(form["custom_headers"])
        self.assertIn("data-kv-headers-widget", rendered)
        self.assertIn("kv-headers-add", rendered)
        self.assertIn("kv-headers-source", rendered)

        valid_form = OpenAIAgentForm(
            data=self._base_post_data(custom_headers='{\n  "X-Env": "prod"\n}'),
            instance=self.agent,
        )
        self.assertTrue(valid_form.is_valid(), valid_form.errors)
        saved = valid_form.save()
        self.assertEqual(saved.custom_headers, '{"X-Env": "prod"}')

        invalid_form = OpenAIAgentForm(
            data=self._base_post_data(
                custom_headers=json.dumps({"X-Bad": " leading-space"})
            ),
            instance=self.agent,
        )
        self.assertFalse(invalid_form.is_valid())
        self.assertIn("custom_headers", invalid_form.errors)

    def test_widget_html_escaping_prevents_textarea_breakout(self):
        payload = json.dumps(
            {"X-XSS": '</textarea><script>alert(1)</script>"&'}
        )
        rendered = CustomHeadersWidget().render("custom_headers", payload)
        self.assertNotIn("</textarea><script>alert(1)</script>", rendered)
        self.assertIn(
            "&lt;/textarea&gt;&lt;script&gt;alert(1)&lt;/script&gt;", rendered
        )
        self.assertIn("&quot;", rendered)
        self.assertIn("&amp;", rendered)

        soup = BeautifulSoup(rendered, "html.parser")
        textareas = soup.select("textarea.kv-headers-source")
        self.assertEqual(len(textareas), 1)
        # Django's textarea template emits a single leading newline per HTML5 spec.
        self.assertEqual(textareas[0].get_text().removeprefix("\n"), payload)
        self.assertFalse(
            any("alert(1)" in script.get_text() for script in soup.find_all("script"))
        )

    def test_admin_add_and_change_pages_render_kv_widget(self):
        add_url = reverse("admin:core_openaiagent_add")
        add_response = self.client.get(add_url)
        self.assertEqual(add_response.status_code, 200)
        self.assertContains(add_response, "data-kv-headers-widget")
        self.assertContains(add_response, "kv-headers-add")

        change_url = reverse("admin:core_openaiagent_change", args=[self.agent.pk])
        change_response = self.client.get(change_url)
        self.assertEqual(change_response.status_code, 200)
        self.assertContains(change_response, "data-kv-headers-widget")
        self.assertContains(change_response, "kv-headers-add")

    @patch("core.admin.agent_admin.task_manager.submit_task")
    def test_admin_add_post_valid_and_invalid_custom_headers(self, mock_submit_task):
        add_url = reverse("admin:core_openaiagent_add")

        invalid_response = self.client.post(
            add_url,
            self._base_post_data(
                name="Invalid Added Agent",
                custom_headers=json.dumps({"X-Bad": "café"}),
            ),
        )
        self.assertEqual(invalid_response.status_code, 200)
        form_errors = invalid_response.context["adminform"].form.errors
        self.assertIn("custom_headers", form_errors)
        self.assertIn("ASCII", " ".join(form_errors["custom_headers"]))
        self._assert_advanced_tab_has_error(invalid_response, "ASCII")
        self.assertFalse(
            OpenAIAgent.objects.filter(name="Invalid Added Agent").exists()
        )
        mock_submit_task.assert_not_called()

        headers_json = json.dumps({"CF-Access-Client-Id": "new-agent-id"})
        with self.captureOnCommitCallbacks(execute=True):
            valid_response = self.client.post(
                add_url,
                self._base_post_data(
                    name="Valid Added Agent",
                    custom_headers=headers_json,
                ),
            )
        self.assertEqual(valid_response.status_code, 302)
        created = OpenAIAgent.objects.get(name="Valid Added Agent")
        self.assertEqual(
            created.get_custom_headers(),
            {"CF-Access-Client-Id": "new-agent-id"},
        )
        mock_submit_task.assert_called_once()

    @patch("core.admin.agent_admin.task_manager.submit_task")
    def test_admin_post_valid_custom_headers_saves_and_schedules_validation(
        self, mock_submit_task
    ):
        url = reverse("admin:core_openaiagent_change", args=[self.agent.pk])
        headers_json = json.dumps(
            {
                "CF-Access-Client-Id": "id-xyz",
                "CF-Access-Client-Secret": "sec-xyz",
            }
        )
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                url, self._base_post_data(custom_headers=headers_json)
            )

        self.assertEqual(response.status_code, 302)
        self.agent.refresh_from_db()
        self.assertEqual(
            self.agent.get_custom_headers(),
            {
                "CF-Access-Client-Id": "id-xyz",
                "CF-Access-Client-Secret": "sec-xyz",
            },
        )
        mock_submit_task.assert_called_once()

    def test_admin_post_authorization_header_rejected_with_error_tab_support(self):
        url = reverse("admin:core_openaiagent_change", args=[self.agent.pk])
        response = self.client.post(
            url,
            self._base_post_data(
                custom_headers=json.dumps({"Authorization": "Bearer bad"})
            ),
        )
        self.assertEqual(response.status_code, 200)
        form_errors = response.context["adminform"].form.errors
        self.assertIn("custom_headers", form_errors)
        error_text = " ".join(form_errors["custom_headers"])
        self.assertIn("Authorization", error_text)
        self.assertIn("API Key", error_text)
        self._assert_advanced_tab_has_error(response, "Authorization")

        self.agent.refresh_from_db()
        self.assertEqual(self.agent.get_custom_headers(), {})

    def test_admin_post_extra_headers_in_advanced_params_rejected(self):
        url = reverse("admin:core_openaiagent_change", args=[self.agent.pk])
        response = self.client.post(
            url,
            self._base_post_data(
                advanced_params=json.dumps({"extra_headers": {"X-Foo": "Bar"}})
            ),
        )
        self.assertEqual(response.status_code, 200)
        form_errors = response.context["adminform"].form.errors
        self.assertIn("advanced_params", form_errors)
        self.assertIn("Custom HTTP Headers", " ".join(form_errors["advanced_params"]))
        self._assert_advanced_tab_has_error(response, "Custom HTTP Headers")

    def test_admin_post_malformed_json_preserves_db_and_raw_textarea(self):
        self.agent.custom_headers = json.dumps({"X-Saved": "keep-me"})
        self.agent.save(update_fields=["custom_headers"])

        malformed = "{not valid json"
        url = reverse("admin:core_openaiagent_change", args=[self.agent.pk])
        response = self.client.post(
            url,
            self._base_post_data(custom_headers=malformed),
        )
        self.assertEqual(response.status_code, 200)
        form_errors = response.context["adminform"].form.errors
        self.assertIn("custom_headers", form_errors)
        self._assert_advanced_tab_has_error(response, "valid JSON object")

        soup = BeautifulSoup(response.content, "html.parser")
        textarea = soup.select_one('textarea[name="custom_headers"]')
        self.assertIsNotNone(textarea)
        self.assertEqual(textarea.get_text().removeprefix("\n"), malformed)

        self.agent.refresh_from_db()
        self.assertEqual(self.agent.get_custom_headers(), {"X-Saved": "keep-me"})

    @patch("core.admin.agent_admin.task_manager.submit_task")
    @patch("core.models.agent.OpenAI")
    def test_legacy_unencrypted_empty_row_and_clearing_saved_headers(
        self, mock_openai, _mock_submit_task
    ):
        # Simulate pre-existing migrated row with literal unencrypted "" in DB.
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE core_openaiagent SET custom_headers = '' WHERE id = %s",
                [self.agent.pk],
            )

        reloaded = OpenAIAgent.objects.get(pk=self.agent.pk)
        self.assertEqual(reloaded.custom_headers, "")
        self.assertEqual(reloaded.get_custom_headers(), {})

        reloaded._init()
        self.assertEqual(
            mock_openai.call_args.kwargs["default_headers"],
            OpenAIAgent.EXTRA_HEADERS,
        )

        change_url = reverse("admin:core_openaiagent_change", args=[self.agent.pk])
        get_response = self.client.get(change_url)
        self.assertEqual(get_response.status_code, 200)

        # Save non-empty headers, then clear back to "".
        reloaded.custom_headers = json.dumps({"X-Temp": "1"})
        reloaded.save(update_fields=["custom_headers"])
        self.assertEqual(reloaded.get_custom_headers(), {"X-Temp": "1"})

        with self.captureOnCommitCallbacks(execute=True):
            clear_response = self.client.post(
                change_url,
                self._base_post_data(custom_headers=""),
            )
        self.assertEqual(clear_response.status_code, 302)
        reloaded.refresh_from_db()
        self.assertEqual(reloaded.custom_headers, "")
        self.assertEqual(reloaded.get_custom_headers(), {})

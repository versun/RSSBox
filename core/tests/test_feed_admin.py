import logging
from unittest.mock import MagicMock, patch

from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory, TestCase
from django.utils import timezone
from django.db import DatabaseError
from django.core.exceptions import ValidationError

from core.admin.feed_admin import FeedAdmin
from django.contrib.auth.models import User

from core.models import Feed

logging.disable(logging.CRITICAL)


class FeedAdminSaveModelTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.admin = FeedAdmin(model=Feed, admin_site=AdminSite())
        self.user = User.objects.create_superuser("admin", "admin@test.com", "password")
        self.feed = Feed.objects.create(
            name="Test Feed",
            feed_url="http://test.com/rss",
            target_language="zh-hans",
        )

    @patch("core.admin.feed_admin.FeedAdmin._submit_feed_update_task")
    def test_save_model_no_reprocessing_needed(self, mock_submit_task):
        """Test save_model when no fields that require reprocessing are changed."""
        request = self.factory.post("/")
        request.user = self.user

        form = MagicMock()
        form.changed_data = ["name"]  # A field that doesn't trigger reprocessing

        self.admin.save_model(request, self.feed, form, True)

        self.feed.refresh_from_db()
        self.assertIsNone(self.feed.fetch_status)
        mock_submit_task.assert_not_called()

    @patch("core.admin.feed_admin.transaction.on_commit")
    @patch("core.admin.feed_admin.FeedAdmin._submit_feed_update_task")
    def test_save_model_feed_url_changed(self, mock_submit_task, mock_on_commit):
        """Test save_model when feed_url is changed."""
        request = self.factory.post("/")
        request.user = self.user

        # Add an entry to the feed to check if it gets deleted
        self.feed.entries.create(
            original_title="Old Entry", link="http://test.com/entry1"
        )
        self.assertEqual(self.feed.entries.count(), 1)

        form = MagicMock()
        form.changed_data = ["feed_url"]

        self.admin.save_model(request, self.feed, form, True)

        # Check that the on_commit hook was set up
        mock_on_commit.assert_called_once()
        # Manually trigger the callback to simulate the transaction commit
        commit_callback = mock_on_commit.call_args.args[0]
        commit_callback()

        self.feed.refresh_from_db()
        self.assertIsNone(self.feed.fetch_status)
        self.assertIsNone(self.feed.translation_status)
        self.assertEqual(self.feed.entries.count(), 0)  # Entries should be deleted
        mock_submit_task.assert_called_once_with(self.feed)

    @patch("core.admin.feed_admin.transaction.on_commit")
    @patch("core.admin.feed_admin.FeedAdmin._submit_feed_update_task")
    def test_save_model_target_language_changed(self, mock_submit_task, mock_on_commit):
        """Test save_model when target_language is changed."""
        request = self.factory.post("/")
        request.user = self.user

        entry = self.feed.entries.create(
            original_title="Old Entry",
            link="http://test.com/entry1",
            translated_title="Translated Title",
            translated_content="Translated Content",
            ai_summary="AI Summary",
        )
        self.assertEqual(self.feed.entries.count(), 1)

        form = MagicMock()
        form.changed_data = ["target_language"]

        self.admin.save_model(request, self.feed, form, True)

        mock_on_commit.assert_called_once()
        commit_callback = mock_on_commit.call_args.args[0]
        commit_callback()

        self.feed.refresh_from_db()
        entry.refresh_from_db()

        self.assertIsNone(self.feed.fetch_status)
        self.assertIsNone(self.feed.translation_status)
        self.assertEqual(self.feed.entries.count(), 1)  # Entries should not be deleted
        self.assertIsNone(entry.translated_title)
        self.assertIsNone(entry.translated_content)
        self.assertIsNone(entry.ai_summary)
        mock_submit_task.assert_called_once_with(self.feed)

    @patch("core.admin.feed_admin.transaction.on_commit")
    @patch("core.admin.feed_admin.FeedAdmin._submit_feed_update_task")
    def test_save_model_other_reprocessing_fields_changed(
        self, mock_submit_task, mock_on_commit
    ):
        """Test save_model for other fields that trigger reprocessing."""
        reprocessing_fields = [
            "translator_option",
            "summarizer",
            "additional_prompt",
        ]
        for field in reprocessing_fields:
            with self.subTest(field=field):
                mock_submit_task.reset_mock()
                mock_on_commit.reset_mock()

                entry = self.feed.entries.create(
                    original_title="Test Entry", translated_title="Translated"
                )

                request = self.factory.post("/")
                request.user = self.user
                form = MagicMock()
                form.changed_data = [field]

                self.admin.save_model(request, self.feed, form, True)

                mock_on_commit.assert_called_once()
                commit_callback = mock_on_commit.call_args.args[0]
                commit_callback()

                entry.refresh_from_db()
                self.assertEqual(
                    entry.translated_title, "Translated"
                )  # Content not cleared
                self.assertEqual(self.feed.entries.count(), 1)
                mock_submit_task.assert_called_once_with(self.feed)

                self.feed.entries.all().delete()  # Clean up for next subtest

    def test_save_model_empty_name_handling(self):
        """Test save_model handles empty name correctly."""
        request = self.factory.post("/")
        request.user = self.user

        form = MagicMock()
        form.changed_data = ["name"]

        # Test with empty name
        self.feed.name = ""
        self.admin.save_model(request, self.feed, form, True)
        self.assertEqual(self.feed.name, "Empty")

        # Test with None name
        self.feed.name = None
        self.admin.save_model(request, self.feed, form, True)
        self.assertEqual(self.feed.name, "Empty")

    def test_save_model_multiple_fields_changed(self):
        """Test save_model when multiple fields are changed."""
        request = self.factory.post("/")
        request.user = self.user

        form = MagicMock()
        form.changed_data = ["feed_url", "target_language", "translator_option"]

        self.admin.save_model(request, self.feed, form, True)

        self.feed.refresh_from_db()
        self.assertIsNone(self.feed.fetch_status)
        self.assertIsNone(self.feed.translation_status)

    @patch("core.admin.feed_admin.transaction.on_commit")
    @patch("core.admin.feed_admin.FeedAdmin._submit_feed_update_task")
    def test_save_model_translation_fields_changed(
        self, mock_submit_task, mock_on_commit
    ):
        """Test save_model when translation fields are changed."""
        request = self.factory.post("/")
        request.user = self.user

        form = MagicMock()
        form.changed_data = ["translate_title"]

        self.admin.save_model(request, self.feed, form, True)

        mock_on_commit.assert_called_once()
        commit_callback = mock_on_commit.call_args.args[0]
        commit_callback()

        self.feed.refresh_from_db()
        self.assertIsNone(self.feed.fetch_status)
        self.assertIsNone(self.feed.translation_status)
        mock_submit_task.assert_called_once_with(self.feed)

    @patch("core.admin.feed_admin.transaction.on_commit")
    @patch("core.admin.feed_admin.FeedAdmin._submit_feed_update_task")
    def test_save_model_summarizer_changed(self, mock_submit_task, mock_on_commit):
        """Test save_model when summarizer field is changed."""
        request = self.factory.post("/")
        request.user = self.user

        form = MagicMock()
        form.changed_data = ["summarizer"]

        self.admin.save_model(request, self.feed, form, True)

        mock_on_commit.assert_called_once()
        commit_callback = mock_on_commit.call_args.args[0]
        commit_callback()

        self.feed.refresh_from_db()
        self.assertIsNone(self.feed.fetch_status)
        self.assertIsNone(self.feed.translation_status)
        mock_submit_task.assert_called_once_with(self.feed)


class FeedAdminViewTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.admin = FeedAdmin(model=Feed, admin_site=AdminSite())
        self.user = User.objects.create_superuser("admin", "admin@test.com", "password")

    def test_changelist_view_adds_import_button(self):
        """Test that changelist_view adds the import OPML button to context."""
        request = self.factory.get("/")
        request.user = self.user

        response = self.admin.changelist_view(request)

        self.assertIn("import_opml_button", response.context_data)
        self.assertIn(
            "import_opml/",
            response.context_data["import_opml_button"],
        )

    def test_changelist_view_with_extra_context(self):
        """Test changelist_view with existing extra_context."""
        request = self.factory.get("/")
        request.user = self.user

        extra_context = {"existing_key": "existing_value"}
        response = self.admin.changelist_view(request, extra_context)

        self.assertIn("import_opml_button", response.context_data)
        self.assertIn("existing_key", response.context_data)
        self.assertEqual(response.context_data["existing_key"], "existing_value")

    def test_get_urls_includes_custom_urls(self):
        """Test that get_urls includes custom import_opml URL."""
        urls = self.admin.get_urls()
        url_names = [url.name for url in urls if hasattr(url, "name")]

        self.assertIn("core_feed_import_opml", url_names)

        # Check that the custom URL is properly configured
        import_url = None
        for url in urls:
            if hasattr(url, "name") and url.name == "core_feed_import_opml":
                import_url = url
                break

        self.assertIsNotNone(import_url)
        self.assertIn("import_opml", str(import_url.pattern))


class FeedAdminDisplayMethodsTest(TestCase):
    """Test display methods in FeedAdmin"""

    def setUp(self):
        self.factory = RequestFactory()
        self.admin = FeedAdmin(model=Feed, admin_site=AdminSite())
        self.user = User.objects.create_superuser("admin", "admin@test.com", "password")
        self.feed = Feed.objects.create(
            name="Test Feed",
            feed_url="http://test.com/rss",
            target_language="zh-hans",
            slug="test-feed",
            update_frequency=30,
            total_tokens=1500,
            total_characters=50000,
            last_fetch=timezone.now(),
            translate_title=True,
            translate_content=True,
            summary=False,
            log="Test log content",
        )

    @patch("core.tasks.task_manager.task_manager.submit_task")
    def test_submit_feed_update_task(self, mock_submit_task):
        """Test _submit_feed_update_task method (lines 190-193)."""
        mock_submit_task.return_value = "task-123"

        self.admin._submit_feed_update_task(self.feed)

        mock_submit_task.assert_called_once()
        args = mock_submit_task.call_args
        self.assertEqual(args[0][0], f"update_feed_{self.feed.slug}")

    def test_simple_update_frequency_cases(self):
        """Test simple_update_frequency for different time intervals."""
        test_cases = [
            (5, "5 min"),
            (15, "15 min"),
            (30, "30 min"),
            (60, "hourly"),
            (1440, "daily"),
            (10080, "weekly"),
        ]

        for frequency, expected in test_cases:
            with self.subTest(frequency=frequency):
                self.feed.update_frequency = frequency
                result = self.admin.simple_update_frequency(self.feed)
                self.assertEqual(result, expected)

    def test_simple_update_frequency_edge_cases(self):
        """Test simple_update_frequency for edge cases."""
        # Test boundary values
        self.feed.update_frequency = 1
        self.assertEqual(self.admin.simple_update_frequency(self.feed), "5 min")

        self.feed.update_frequency = 4
        self.assertEqual(self.admin.simple_update_frequency(self.feed), "5 min")

        self.feed.update_frequency = 6
        self.assertEqual(self.admin.simple_update_frequency(self.feed), "15 min")

        # Test large values - should return None for values > 10080
        self.feed.update_frequency = 10081
        self.assertIsNone(self.admin.simple_update_frequency(self.feed))

    def test_translator_method(self):
        """Test translator method (line 212)."""
        result = self.admin.translator(self.feed)
        self.assertEqual(result, self.feed.translator)

    @patch("core.admin.feed_admin.status_icon")
    def test_generate_feed_scenarios(self, mock_status_icon):
        """Test generate_feed for different translation scenarios."""
        # Test no translation
        self.feed.translate_title = False
        self.feed.translate_content = False
        self.feed.summary = False

        result = self.admin.generate_feed(self.feed)
        self.assertIn("-", result)
        self.assertIn(f"/rss/{self.feed.slug}", result)
        self.assertIn(f"/rss/json/{self.feed.slug}", result)

        # Test with translation
        mock_status_icon.return_value = "✓"
        self.feed.translate_title = True
        self.feed.translation_status = True

        result = self.admin.generate_feed(self.feed)
        mock_status_icon.assert_called_with(self.feed.translation_status)
        self.assertIn("✓", result)
        self.assertIn(f"/rss/{self.feed.slug}", result)

    @patch("core.admin.feed_admin.status_icon")
    def test_generate_feed_mixed_translation_scenarios(self, mock_status_icon):
        """Test generate_feed with mixed translation settings."""
        mock_status_icon.return_value = "✓"

        # Test only title translation
        self.feed.translate_title = True
        self.feed.translate_content = False
        self.feed.summary = False

        result = self.admin.generate_feed(self.feed)
        self.assertIn("✓", result)
        self.assertIn(f"/rss/{self.feed.slug}", result)

        # Test only content translation
        self.feed.translate_title = False
        self.feed.translate_content = True
        self.feed.summary = False

        result = self.admin.generate_feed(self.feed)
        self.assertIn("✓", result)
        self.assertIn(f"/rss/{self.feed.slug}", result)

        # Test only summary
        self.feed.translate_title = False
        self.feed.translate_content = False
        self.feed.summary = True

        result = self.admin.generate_feed(self.feed)
        self.assertIn("✓", result)
        self.assertIn(f"/rss/{self.feed.slug}", result)

    def test_fetch_feed_scenarios(self):
        """Test fetch_feed method with and without pk."""
        # Test with existing pk
        self.feed.fetch_status = True
        result = self.admin.fetch_feed(self.feed)
        self.assertIn(self.feed.feed_url, result)
        self.assertIn(f"/rss/proxy/{self.feed.slug}", result)
        self.assertIn("url", result)
        self.assertIn("proxy", result)

        # Test without pk
        new_feed = Feed(name="Unsaved Feed", feed_url="http://test.com/rss")
        result = self.admin.fetch_feed(new_feed)
        self.assertIn(new_feed.feed_url, result)
        self.assertIn("url", result)
        self.assertIn("proxy", result)

    @patch("core.admin.feed_admin.status_icon")
    def test_fetch_feed_with_different_statuses(self, mock_status_icon):
        """Test fetch_feed with different fetch_status values."""
        mock_status_icon.return_value = "✓"

        # Test with True status
        self.feed.fetch_status = True
        result = self.admin.fetch_feed(self.feed)
        # status_icon is called twice in fetch_feed method
        self.assertEqual(mock_status_icon.call_count, 1)
        self.assertIn("✓", result)

        # Reset mock for next test
        mock_status_icon.reset_mock()
        mock_status_icon.return_value = "✗"

        # Test with False status
        self.feed.fetch_status = False
        result = self.admin.fetch_feed(self.feed)
        self.assertEqual(mock_status_icon.call_count, 1)
        self.assertIn("✗", result)

        # Reset mock for next test
        mock_status_icon.reset_mock()
        mock_status_icon.return_value = "⏳"

        # Test with None status
        self.feed.fetch_status = None
        result = self.admin.fetch_feed(self.feed)
        self.assertEqual(mock_status_icon.call_count, 1)
        self.assertIn("⏳", result)

    def test_show_log_method(self):
        """Test show_log method (line 265)."""
        result = self.admin.show_log(self.feed)

        self.assertIn("<details>", result)
        self.assertIn("Test log content", result)
        self.assertIn("<summary>show</summary>", result)

    def test_show_log_with_empty_log(self):
        """Test show_log method with empty log content."""
        self.feed.log = ""
        result = self.admin.show_log(self.feed)

        self.assertIn("<details>", result)
        self.assertIn("<summary>show</summary>", result)
        self.assertIn("</div>", result)

    def test_show_log_with_none_log(self):
        """Test show_log method with None log content."""
        self.feed.log = None
        result = self.admin.show_log(self.feed)

        self.assertIn("<details>", result)
        self.assertIn("<summary>show</summary>", result)
        self.assertIn("</div>", result)

    def test_fetch_info_method(self):
        """Test fetch_info method (line 279)."""
        result = self.admin.fetch_info(self.feed)

        self.assertIn("30 min", result)  # from update_frequency
        self.assertIn(self.feed.last_fetch.strftime("%Y-%m-%d %H:%M:%S"), result)

    def test_fetch_info_without_last_fetch(self):
        """Test fetch_info method when last_fetch is None."""
        self.feed.last_fetch = None
        result = self.admin.fetch_info(self.feed)

        self.assertIn("30 min", result)  # from update_frequency
        self.assertIn("-", result)  # for None last_fetch

    def test_cost_info_formatting(self):
        """Test cost_info method for different number formats."""
        test_cases = [
            (500, 800, "tokens:500", "characters:800"),
            (2500, 15000, "tokens:2.5K", "characters:15K"),
            (2500000, 15000000, "tokens:2.5M", "characters:15M"),
        ]

        for tokens, chars, expected_tokens, expected_chars in test_cases:
            with self.subTest(tokens=tokens, chars=chars):
                self.feed.total_tokens = tokens
                self.feed.total_characters = chars
                result = self.admin.cost_info(self.feed)
                self.assertIn(expected_tokens, result)
                self.assertIn(expected_chars, result)

    def test_cost_info_edge_cases(self):
        """Test cost_info method for edge cases."""
        # Test zero values
        self.feed.total_tokens = 0
        self.feed.total_characters = 0
        result = self.admin.cost_info(self.feed)
        self.assertIn("tokens:0", result)
        self.assertIn("characters:0", result)

        # Test exact boundary values
        self.feed.total_tokens = 1000
        self.feed.total_characters = 1000
        result = self.admin.cost_info(self.feed)
        self.assertIn("tokens:1K", result)
        self.assertIn("characters:1K", result)

        # Test values just below boundaries
        self.feed.total_tokens = 999
        self.feed.total_characters = 999
        result = self.admin.cost_info(self.feed)
        self.assertIn("tokens:999", result)
        self.assertIn("characters:999", result)

    def test_show_filters_scenarios(self):
        """Test show_filters with and without filters."""
        # Test no filters
        result = self.admin.show_filters(self.feed)
        self.assertEqual(result, "-")

        # Test with filters
        from core.models import Filter

        filter1 = Filter.objects.create(name="Test Filter 1")
        filter2 = Filter.objects.create(name="Test Filter 2")
        self.feed.filters.add(filter1, filter2)

        result = self.admin.show_filters(self.feed)
        self.assertIn("Test Filter 1", result)
        self.assertIn("Test Filter 2", result)
        self.assertIn(f"/core/filter/{filter1.id}/change/", result)

    def test_show_filters_single_filter(self):
        """Test show_filters with single filter."""
        from core.models import Filter

        filter1 = Filter.objects.create(name="Single Filter")
        self.feed.filters.add(filter1)

        result = self.admin.show_filters(self.feed)
        self.assertIn("Single Filter", result)
        self.assertNotIn("<br>", result)  # No line break for single filter

    def test_show_tags_scenarios(self):
        """Test show_tags with and without tags."""
        # Test no tags
        feed_without_tags = Feed.objects.create(
            name="Feed Without Tags",
            feed_url="http://example.com/rss",
            target_language="en",
        )
        result = self.admin.show_tags(feed_without_tags)
        self.assertIn(result, ["", "-"])

        # Test with tags
        from core.models import Tag

        tag1 = Tag.objects.create(name="Tag1")
        tag2 = Tag.objects.create(name="Tag2")
        self.feed.tags.add(tag1, tag2)

        result = self.admin.show_tags(self.feed)
        self.assertIn("#Tag1", result)
        self.assertIn("#Tag2", result)
        self.assertIn(f"/core/tag/{tag1.id}/change/", result)

    def test_show_tags_single_tag(self):
        """Test show_tags with single tag."""
        from core.models import Tag

        tag1 = Tag.objects.create(name="SingleTag")
        self.feed.tags.add(tag1)

        result = self.admin.show_tags(self.feed)
        self.assertIn("#SingleTag", result)
        self.assertNotIn("<br>", result)  # No line break for single tag

    def test_show_tags_with_empty_tags(self):
        """Test show_tags with empty tags queryset."""
        # Create a feed with no tags
        feed_without_tags = Feed.objects.create(
            name="Feed Without Tags",
            feed_url="http://example.com/rss",
            target_language="en",
        )

        result = self.admin.show_tags(feed_without_tags)
        # Empty tags should return "-"
        self.assertEqual(result, "-")


class FeedAdminErrorHandlingTest(TestCase):
    """Test error handling scenarios in FeedAdmin"""

    def setUp(self):
        self.factory = RequestFactory()
        self.admin = FeedAdmin(model=Feed, admin_site=AdminSite())
        self.user = User.objects.create_superuser("admin", "admin@test.com", "password")
        self.feed = Feed.objects.create(
            name="Test Feed",
            feed_url="http://test.com/rss",
            target_language="zh-hans",
        )

    @patch("core.admin.feed_admin.transaction.on_commit")
    @patch("core.admin.feed_admin.FeedAdmin._submit_feed_update_task")
    def test_save_model_database_error_handling(self, mock_submit_task, mock_on_commit):
        """Test save_model handles database errors gracefully."""
        request = self.factory.post("/")
        request.user = self.user

        form = MagicMock()
        form.changed_data = ["feed_url"]

        # Mock database error during save
        with patch.object(Feed, "save", side_effect=DatabaseError("Database error")):
            with self.assertRaises(DatabaseError):
                self.admin.save_model(request, self.feed, form, True)

        # Verify that task submission was not attempted
        mock_on_commit.assert_not_called()

    @patch("core.admin.feed_admin.transaction.on_commit")
    @patch("core.admin.feed_admin.FeedAdmin._submit_feed_update_task")
    def test_save_model_validation_error_handling(
        self, mock_submit_task, mock_on_commit
    ):
        """Test save_model handles validation errors gracefully."""
        request = self.factory.post("/")
        request.user = self.user

        form = MagicMock()
        form.changed_data = ["feed_url"]

        # Mock validation error during save
        with patch.object(
            Feed, "save", side_effect=ValidationError("Validation error")
        ):
            with self.assertRaises(ValidationError):
                self.admin.save_model(request, self.feed, form, True)

        # Verify that task submission was not attempted
        mock_on_commit.assert_not_called()

    @patch("core.tasks.task_manager.task_manager.submit_task")
    def test_submit_feed_update_task_error_handling(self, mock_submit_task):
        """Test _submit_feed_update_task handles task submission errors."""
        # Mock task submission error
        mock_submit_task.side_effect = Exception("Task submission failed")

        # Should raise exception since there's no error handling in the method
        with self.assertRaises(Exception):
            self.admin._submit_feed_update_task(self.feed)


class FeedAdminIntegrationTest(TestCase):
    """Integration tests for FeedAdmin functionality"""

    def setUp(self):
        self.factory = RequestFactory()
        self.admin = FeedAdmin(model=Feed, admin_site=AdminSite())
        self.user = User.objects.create_superuser("admin", "admin@test.com", "password")

    def test_fieldsets_configuration(self):
        """Test that fieldsets are properly configured."""
        expected_fieldsets = [
            (
                "Feed Information",
                {
                    "fields": (
                        "feed_url",
                        "name",
                        "max_posts",
                        "simple_update_frequency",
                        "tags",
                        "fetch_article",
                        "show_log",
                    )
                },
            ),
            (
                "Content Processing",
                {
                    "fields": (
                        "target_language",
                        "translate_title",
                        "translate_content",
                        "summary",
                        "translator_option",
                        "summarizer",
                        "summary_detail",
                        "additional_prompt",
                    )
                },
            ),
            ("Output Control", {"fields": ("slug", "translation_display", "filters")}),
            (
                "Status",
                {
                    "fields": (
                        "fetch_status",
                        "translation_status",
                        "total_tokens",
                        "total_characters",
                        "last_fetch",
                        "last_translate",
                    )
                },
            ),
        ]

        self.assertEqual(len(self.admin.fieldsets), len(expected_fieldsets))

        for i, (expected_name, expected_config) in enumerate(expected_fieldsets):
            actual_name, actual_config = self.admin.fieldsets[i]
            self.assertEqual(actual_name, expected_name)
            self.assertEqual(actual_config["fields"], expected_config["fields"])

    def test_change_form_template(self):
        """Test that change_form_template is properly set."""
        self.assertEqual(
            self.admin.change_form_template, "admin/change_form_with_tabs.html"
        )

    def test_form_class(self):
        """Test that form class is properly set."""
        from core.forms import FeedForm

        self.assertEqual(self.admin.form, FeedForm)

    def test_list_per_page(self):
        """Test that list_per_page is properly set."""
        self.assertEqual(self.admin.list_per_page, 20)

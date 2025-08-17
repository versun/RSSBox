import logging
from unittest.mock import MagicMock, patch

from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory, TestCase
from django.utils import timezone

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
            "summary_engine_option",
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
            log="Test log content"
        )

    @patch("utils.task_manager.task_manager.submit_task")
    def test_submit_feed_update_task(self, mock_submit_task):
        """Test _submit_feed_update_task method (lines 190-193)."""
        mock_submit_task.return_value = "task-123"
        
        self.admin._submit_feed_update_task(self.feed)
        
        mock_submit_task.assert_called_once()
        args = mock_submit_task.call_args
        self.assertEqual(args[0][0], f"Update Feed: {self.feed.name}")

    def test_simple_update_frequency_cases(self):
        """Test simple_update_frequency for different time intervals."""
        test_cases = [
            (5, "5 min"),
            (15, "15 min"),
            (30, "30 min"),
            (60, "hourly"),
            (1440, "daily"),
            (10080, "weekly")
        ]
        
        for frequency, expected in test_cases:
            with self.subTest(frequency=frequency):
                self.feed.update_frequency = frequency
                result = self.admin.simple_update_frequency(self.feed)
                self.assertEqual(result, expected)

    def test_translator_method(self):
        """Test translator method (line 212)."""
        result = self.admin.translator(self.feed)
        self.assertEqual(result, self.feed.translator)

    @patch('core.admin.feed_admin.status_icon')
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

    def test_translation_options_display(self):
        """Test translation_options display method (lines 246-253)."""
        result = self.admin.translation_options(self.feed)
        
        # Should show green circles for enabled options
        self.assertIn("🟢", result)  # translate_title is True
        self.assertIn("⚪", result)  # summary is False

    def test_show_log_method(self):
        """Test show_log method (line 265)."""
        result = self.admin.show_log(self.feed)
        
        self.assertIn("<details>", result)
        self.assertIn("Test log content", result)
        self.assertIn("<summary>show</summary>", result)

    def test_fetch_info_method(self):
        """Test fetch_info method (line 279)."""
        result = self.admin.fetch_info(self.feed)
        
        self.assertIn("30 min", result)  # from update_frequency
        self.assertIn(self.feed.last_fetch.strftime("%Y-%m-%d %H:%M:%S"), result)

    def test_cost_info_formatting(self):
        """Test cost_info method for different number formats."""
        test_cases = [
            (500, 800, "tokens:500", "characters:800"),
            (2500, 15000, "tokens:2.5K", "characters:15K"),
            (2500000, 15000000, "tokens:2.5M", "characters:15M")
        ]
        
        for tokens, chars, expected_tokens, expected_chars in test_cases:
            with self.subTest(tokens=tokens, chars=chars):
                self.feed.total_tokens = tokens
                self.feed.total_characters = chars
                result = self.admin.cost_info(self.feed)
                self.assertIn(expected_tokens, result)
                self.assertIn(expected_chars, result)

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

    def test_show_tags_scenarios(self):
        """Test show_tags with and without tags."""
        # Test no tags
        feed_without_tags = Feed.objects.create(
            name="Feed Without Tags",
            feed_url="http://example.com/rss",
            target_language="en"
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

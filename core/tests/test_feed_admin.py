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
        self.user = User.objects.create_superuser(
            "admin", "admin@test.com", "password"
        )
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
        self.feed.entries.create(original_title="Old Entry", link="http://test.com/entry1")
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
    def test_save_model_other_reprocessing_fields_changed(self, mock_submit_task, mock_on_commit):
        """Test save_model for other fields that trigger reprocessing."""
        reprocessing_fields = [
            'translator_option',
            'summary_engine_option',
            'additional_prompt',
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
                self.assertEqual(entry.translated_title, "Translated")  # Content not cleared
                self.assertEqual(self.feed.entries.count(), 1)
                mock_submit_task.assert_called_once_with(self.feed)

                self.feed.entries.all().delete() # Clean up for next subtest


class FeedAdminViewTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.admin = FeedAdmin(model=Feed, admin_site=AdminSite())
        self.user = User.objects.create_superuser(
            "admin", "admin@test.com", "password"
        )

    def test_changelist_view_adds_import_button(self):
        """Test that changelist_view adds the import OPML button to context."""
        request = self.factory.get("/")
        request.user = self.user

        response = self.admin.changelist_view(request)

        self.assertIn("import_opml_button", response.context_data)
        self.assertIn(
            'import_opml/',
            response.context_data["import_opml_button"],
        )

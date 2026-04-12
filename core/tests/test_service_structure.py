from django.test import SimpleTestCase


class ServiceStructureTests(SimpleTestCase):
    def test_feed_service_exports(self):
        from core.services.feed import (
            build_feed_response,
            refresh_updated_content,
            render_feed_content,
            render_tag_content,
            run_feed_update,
        )

        self.assertTrue(callable(run_feed_update))
        self.assertTrue(callable(refresh_updated_content))
        self.assertTrue(callable(render_feed_content))
        self.assertTrue(callable(render_tag_content))
        self.assertTrue(callable(build_feed_response))

    def test_feed_service_hides_internal_helpers(self):
        import core.services.feed as feed_services

        for name in [
            "apply_ai_filter",
            "apply_feed_filters",
            "apply_filter",
            "apply_keywords_filter",
            "apply_tag_filters",
            "needs_re_evaluation",
            "add_atom_entry",
            "build_atom_feed",
            "finalize_atom_feed",
        ]:
            with self.subTest(name=name):
                self.assertFalse(hasattr(feed_services, name))

    def test_admin_service_exports(self):
        from core.services.admin import force_update_feeds, force_update_tags

        self.assertTrue(callable(force_update_feeds))
        self.assertTrue(callable(force_update_tags))

    def test_admin_service_hides_batch_helpers(self):
        import core.services.admin as admin_services

        self.assertFalse(hasattr(admin_services, "apply_batch_updates"))
        self.assertFalse(hasattr(admin_services, "build_batch_modify_context"))

    def test_opml_service_exports(self):
        from core.services.opml import build_opml_response, import_opml_content

        self.assertTrue(callable(import_opml_content))
        self.assertTrue(callable(build_opml_response))

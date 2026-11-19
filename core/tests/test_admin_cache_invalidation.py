"""Cache invalidation for admin edits that bypass the update pipeline.

Editing a feed/tag/digest in the admin used to leave rendered feeds stale
until their TTL when the change did not trigger reprocessing: M2M writes
(filters/tags) happen in save_related, after save_model, and several scalar
fields were never checked either. These tests assert bump_cache_version is
called for each affected scope.
"""

from unittest.mock import MagicMock, patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase

from core.admin.digest_admin import DigestAdmin
from core.admin.feed_admin import FeedAdmin
from core.admin.filter_admin import FilterAdmin
from core.admin.tag_admin import TagAdmin
from core.models import Digest, Feed, Filter, OpenAIAgent, Tag


def _form(instance, changed_data):
    form = MagicMock()
    form.instance = instance
    form.changed_data = changed_data
    return form


class FeedAdminInvalidationTest(TestCase):
    def setUp(self):
        self.admin = FeedAdmin(model=Feed, admin_site=AdminSite())
        self.user = User.objects.create_superuser("admin", "a@b.c", "pw")
        self.request = RequestFactory().post("/")
        self.request.user = self.user
        self.feed = Feed.objects.create(
            name="F", feed_url="https://example.com/f.xml", slug="f"
        )

    def _save(self, changed_data, change=True, save_m2m=None):
        form = _form(self.feed, changed_data)
        if save_m2m is not None:
            form.save_m2m.side_effect = save_m2m
        with (
            patch("core.models.filter.bump_cache_version") as bump,
            self.captureOnCommitCallbacks(execute=True),
        ):
            self.admin.save_related(self.request, form, [], change)
        return bump

    def test_filter_change_bumps_feed_and_only_apply_feed_filters_tags(self):
        opt_in_tag = Tag.objects.create(name="t-opt-in", apply_feed_filters=True)
        opt_out_tag = Tag.objects.create(name="t-opt-out", apply_feed_filters=False)
        self.feed.tags.add(opt_in_tag, opt_out_tag)
        bump = self._save(["filters"])

        bump.assert_any_call("rss", self.feed.pk)
        bump.assert_any_call("tag", opt_in_tag.pk)
        self.assertNotIn(
            ("tag", opt_out_tag.pk), [c.args for c in bump.call_args_list]
        )

    def test_slug_change_bumps_feed_only(self):
        tag = Tag.objects.create(name="t1")
        self.feed.tags.add(tag)
        bump = self._save(["slug"])

        bump.assert_called_once_with("rss", self.feed.pk)

    def test_deleting_feed_invalidates_feed_and_its_tags(self):
        tag = Tag.objects.create(name="t1")
        self.feed.tags.add(tag)
        feed_pk = self.feed.pk
        with (
            patch("core.models.filter.bump_cache_version") as bump,
            self.captureOnCommitCallbacks(execute=True),
        ):
            self.feed.delete()
        calls = [c.args for c in bump.call_args_list]
        self.assertIn(("rss", feed_pk), calls)
        self.assertIn(("tag", tag.pk), calls)

    def test_create_save_model_does_not_bump(self):
        new_feed = Feed(name="New", feed_url="https://example.com/new.xml")
        form = _form(new_feed, ["feed_url", "target_language"])
        with (
            patch("core.models.filter.bump_cache_version") as bump,
            patch.object(self.admin, "_submit_feed_update_task"),
            self.captureOnCommitCallbacks(execute=True),
        ):
            self.admin.save_model(self.request, new_feed, form, False)
        bump.assert_not_called()

    def test_save_model_feed_url_and_target_language_deletes_without_redundant_update(
        self,
    ):
        self.feed.entries.create(
            original_title="Old", link="https://example.com/e1", translated_title="旧"
        )
        form = _form(self.feed, ["feed_url", "target_language"])
        with (
            patch("core.models.filter.bump_cache_version") as bump,
            patch.object(self.admin, "_submit_feed_update_task") as submit,
        ):
            with self.captureOnCommitCallbacks(execute=True):
                self.admin.save_model(self.request, self.feed, form, True)
                # Bump must wait until transaction commit, not fire mid-transaction.
                bump.assert_not_called()
                submit.assert_not_called()
        self.assertEqual(self.feed.entries.count(), 0)
        bump.assert_any_call("rss", self.feed.pk)
        submit.assert_called_once_with(self.feed)

    def test_save_related_defers_bump_until_commit_and_skips_on_rollback(self):
        from django.db import transaction

        form = _form(self.feed, ["slug"])
        with patch("core.models.filter.bump_cache_version") as bump:
            with self.assertRaises(RuntimeError):
                with self.captureOnCommitCallbacks(execute=True):
                    with transaction.atomic():
                        self.admin.save_related(self.request, form, [], True)
                        bump.assert_not_called()
                        raise RuntimeError("abort")
            bump.assert_not_called()

    def test_tag_reassignment_bumps_old_and_new_tags(self):
        old_tag = Tag.objects.create(name="old")
        new_tag = Tag.objects.create(name="new")
        self.feed.tags.add(old_tag)
        bump = self._save(
            ["tags"],
            save_m2m=lambda: self.feed.tags.set([new_tag]),
        )

        bump.assert_any_call("tag", old_tag.pk)
        bump.assert_any_call("tag", new_tag.pk)
        # rss output is unchanged by membership alone.
        self.assertNotIn(("rss", self.feed.pk), [c.args for c in bump.call_args_list])

    def test_max_posts_change_bumps_feed_and_tags(self):
        tag = Tag.objects.create(name="t1")
        self.feed.tags.add(tag)
        bump = self._save(["max_posts"])

        bump.assert_any_call("rss", self.feed.pk)
        bump.assert_any_call("tag", tag.pk)

    def test_reprocessing_edit_defers_rss_bump_but_bumps_tags(self):
        """When reprocessing is triggered, save_model sets translation_status=None;
        bumping rss in save_related would orphan the cached feed while _build_rss
        returns None (404) until update_single_feed finishes."""
        old_tag = Tag.objects.create(name="old")
        new_tag = Tag.objects.create(name="new")
        self.feed.tags.add(old_tag)
        bump = self._save(
            ["additional_prompt", "name", "tags"],
            save_m2m=lambda: self.feed.tags.set([new_tag]),
        )

        self.assertNotIn(("rss", self.feed.pk), [c.args for c in bump.call_args_list])
        bump.assert_any_call("tag", old_tag.pk)
        bump.assert_any_call("tag", new_tag.pk)

    def test_create_does_not_bump(self):
        bump = self._save(["name", "max_posts", "filters", "tags"], change=False)
        bump.assert_not_called()

    def test_unrelated_change_does_not_bump(self):
        bump = self._save(["total_characters"])
        bump.assert_not_called()


class TagAdminInvalidationTest(TestCase):
    def setUp(self):
        self.admin = TagAdmin(Tag, AdminSite())
        self.user = User.objects.create_superuser("admin", "a@b.c", "pw")
        self.request = RequestFactory().post("/")
        self.request.user = self.user
        self.tag = Tag.objects.create(name="t1")

    def test_filter_change_bumps_tag(self):
        form = _form(self.tag, ["filters"])
        with (
            patch("core.models.filter.bump_cache_version") as bump,
            self.captureOnCommitCallbacks(execute=True),
        ):
            self.admin.save_related(self.request, form, [], True)
        bump.assert_called_once_with("tag", self.tag.pk)

    def test_no_filter_change_does_not_bump(self):
        form = _form(self.tag, [])
        with (
            patch("core.models.filter.bump_cache_version") as bump,
            self.captureOnCommitCallbacks(execute=True),
        ):
            self.admin.save_related(self.request, form, [], True)
        bump.assert_not_called()

    def test_create_with_filters_does_not_bump(self):
        form = _form(self.tag, ["filters"])
        with (
            patch("core.models.filter.bump_cache_version") as bump,
            self.captureOnCommitCallbacks(execute=True),
        ):
            self.admin.save_related(self.request, form, [], False)
        bump.assert_not_called()

    def test_none_changed_data_does_not_crash(self):
        form = _form(self.tag, None)
        with (
            patch("core.models.filter.bump_cache_version") as bump,
            self.captureOnCommitCallbacks(execute=True),
        ):
            self.admin.save_related(self.request, form, [], True)
        bump.assert_not_called()

    def test_deleting_tag_invalidates_tag_cache(self):
        tag_pk = self.tag.pk
        with (
            patch("core.models.filter.bump_cache_version") as bump,
            self.captureOnCommitCallbacks(execute=True),
        ):
            self.tag.delete()
        bump.assert_called_once_with("tag", tag_pk)


class FilterAdminInvalidationTest(TestCase):
    def setUp(self):
        self.admin = FilterAdmin(Filter, AdminSite())
        self.user = User.objects.create_superuser("admin", "a@b.c", "pw")
        self.request = RequestFactory().post("/")
        self.request.user = self.user
        self.filt = Filter.objects.create(name="No ads", operation=Filter.EXCLUDE)
        self.feed = Feed.objects.create(
            name="F", feed_url="https://example.com/f.xml", slug="f-filt"
        )
        self.feed.filters.add(self.filt)
        self.direct_tag = Tag.objects.create(name="direct")
        self.direct_tag.filters.add(self.filt)
        self.opt_in_tag = Tag.objects.create(name="opt-in", apply_feed_filters=True)
        self.opt_out_tag = Tag.objects.create(name="opt-out", apply_feed_filters=False)
        self.feed.tags.add(self.opt_in_tag, self.opt_out_tag)

    def test_rule_change_without_reevaluate_bumps_feeds_and_tags(self):
        form = _form(self.filt, ["keywords"])
        form._reevaluated = False
        with (
            patch("core.models.filter.bump_cache_version") as bump,
            self.captureOnCommitCallbacks(execute=True),
        ):
            self.admin.save_related(self.request, form, [], True)
        calls = [c.args for c in bump.call_args_list]
        self.assertIn(("rss", self.feed.pk), calls)
        self.assertIn(("tag", self.direct_tag.pk), calls)
        self.assertIn(("tag", self.opt_in_tag.pk), calls)
        self.assertNotIn(("tag", self.opt_out_tag.pk), calls)

    def test_reevaluate_does_not_double_bump(self):
        form = _form(self.filt, ["keywords", "reevaluate_existing"])
        form._reevaluated = True
        with (
            patch("core.models.filter.bump_cache_version") as bump,
            self.captureOnCommitCallbacks(execute=True),
        ):
            self.admin.save_related(self.request, form, [], True)
        bump.assert_not_called()

    def test_name_only_change_does_not_bump(self):
        form = _form(self.filt, ["name"])
        form._reevaluated = False
        with (
            patch("core.models.filter.bump_cache_version") as bump,
            self.captureOnCommitCallbacks(execute=True),
        ):
            self.admin.save_related(self.request, form, [], True)
        bump.assert_not_called()

    def test_create_does_not_bump(self):
        form = _form(self.filt, ["keywords", "operation"])
        form._reevaluated = False
        with (
            patch("core.models.filter.bump_cache_version") as bump,
            self.captureOnCommitCallbacks(execute=True),
        ):
            self.admin.save_related(self.request, form, [], False)
        bump.assert_not_called()

    def test_deleting_filter_invalidates_feeds_and_tags(self):
        with (
            patch("core.models.filter.bump_cache_version") as bump,
            self.captureOnCommitCallbacks(execute=True),
        ):
            self.filt.delete()
        calls = [c.args for c in bump.call_args_list]
        self.assertIn(("rss", self.feed.pk), calls)
        self.assertIn(("tag", self.direct_tag.pk), calls)
        self.assertIn(("tag", self.opt_in_tag.pk), calls)
        self.assertNotIn(("tag", self.opt_out_tag.pk), calls)

    def test_unsaved_filter_invalidate_is_noop(self):
        with (
            patch("core.models.filter.bump_cache_version") as bump,
            self.captureOnCommitCallbacks(execute=True),
        ):
            Filter(name="Unsaved").invalidate_related_caches()
        bump.assert_not_called()

    def test_real_filter_form_keyword_edit_invalidates_caches(self):
        from core.forms import FilterForm

        form = FilterForm(
            instance=self.filt,
            data={
                "name": self.filt.name,
                "filter_method": Filter.KEYWORD_ONLY,
                "operation": Filter.EXCLUDE,
                "target_field": ["original_title", "original_content"],
                "keywords": "sponsored",
                "filter_prompt": self.filt.filter_prompt or "",
                "total_tokens": 0,
            },
        )
        self.assertTrue(form.is_valid(), form.errors)
        obj = form.save(commit=False)
        self.admin.save_model(self.request, obj, form, True)
        with (
            patch("core.models.filter.bump_cache_version") as bump,
            self.captureOnCommitCallbacks(execute=True),
        ):
            self.admin.save_related(self.request, form, [], True)
        calls = [c.args for c in bump.call_args_list]
        self.assertIn(("rss", self.feed.pk), calls)
        self.assertIn(("tag", self.direct_tag.pk), calls)
        self.assertIn(("tag", self.opt_in_tag.pk), calls)
        self.assertNotIn(("tag", self.opt_out_tag.pk), calls)


class DigestAdminInvalidationTest(TestCase):
    def setUp(self):
        self.admin = DigestAdmin(Digest, AdminSite())
        self.user = User.objects.create_superuser("admin", "a@b.c", "pw")
        self.request = RequestFactory().post("/")
        self.request.user = self.user
        agent = OpenAIAgent.objects.create(name="agent", api_key="k")
        self.digest = Digest.objects.create(name="d", summarizer=agent)

    def test_edit_bumps_digest(self):
        form = _form(self.digest, ["prompt"])
        with (
            patch("core.models.filter.bump_cache_version") as bump,
            self.captureOnCommitCallbacks(execute=True),
        ):
            self.admin.save_related(self.request, form, [], True)
        bump.assert_called_once_with("digest", self.digest.pk)

    def test_create_does_not_bump(self):
        form = _form(self.digest, ["prompt"])
        with (
            patch("core.models.filter.bump_cache_version") as bump,
            self.captureOnCommitCallbacks(execute=True),
        ):
            self.admin.save_related(self.request, form, [], False)
        bump.assert_not_called()

    def test_edit_syncs_digest_feed_and_advances_validators(self):
        from django.core.cache import cache
        from django.utils import timezone
        from core.views import _get_digest_etag, _get_digest_modified

        cache.clear()
        try:
            self.digest.last_generated = timezone.now()
            self.digest.save()
            feed_before = self.digest.get_digest_feed()
            self.assertEqual(feed_before.name, "d")

            etag_before = _get_digest_etag(self.request, self.digest.slug)
            modified_before = _get_digest_modified(self.request, self.digest.slug)

            self.digest.name = "Renamed Digest"
            self.digest.description = "Updated description"
            self.digest.save()
            form = _form(self.digest, ["name", "description"])
            with self.captureOnCommitCallbacks(execute=True):
                self.admin.save_related(self.request, form, [], True)

            feed_after = self.digest.get_digest_feed()
            self.assertEqual(feed_after.pk, feed_before.pk)
            self.assertEqual(feed_after.name, "Renamed Digest")
            self.assertEqual(feed_after.subtitle, "Updated description")
            self.assertNotEqual(
                _get_digest_etag(self.request, self.digest.slug), etag_before
            )
            self.assertGreater(
                _get_digest_modified(self.request, self.digest.slug), modified_before
            )
        finally:
            cache.clear()

    def test_get_digest_feed_truncates_long_description(self):
        self.digest.description = "x" * 300
        self.digest.save()
        feed = self.digest.get_digest_feed()
        self.assertEqual(len(feed.subtitle), 255)

    def test_get_digest_feed_reuses_feed_on_target_language_change(self):
        feed_before = self.digest.get_digest_feed()
        feed_before.entries.create(
            original_title="Issue 1", link="https://example.com/digest/1"
        )
        new_lang = (
            "English" if self.digest.target_language != "English" else "Japanese"
        )
        self.digest.target_language = new_lang
        self.digest.save()

        feed_after = self.digest.get_digest_feed()
        self.assertEqual(feed_after.pk, feed_before.pk)
        self.assertEqual(feed_after.target_language, new_lang)
        self.assertEqual(feed_after.language, new_lang)
        self.assertEqual(feed_after.entries.count(), 1)
        self.assertEqual(
            Feed.objects.filter(feed_url=feed_before.feed_url).count(), 1
        )

    def test_get_digest_feed_and_delete_handle_legacy_duplicate_feeds(self):
        first_feed = self.digest.get_digest_feed()
        other_lang = (
            "English" if self.digest.target_language != "English" else "Japanese"
        )
        second_feed = Feed.objects.create(
            feed_url=first_feed.feed_url,
            target_language=other_lang,
            name="Legacy Orphan",
        )
        self.digest.target_language = other_lang
        self.digest.save()

        # Must not raise MultipleObjectsReturned when legacy duplicates exist.
        picked = self.digest.get_digest_feed()
        self.assertEqual(picked.pk, second_feed.pk)

        with (
            patch("core.models.filter.bump_cache_version") as bump,
            self.captureOnCommitCallbacks(execute=True),
        ):
            digest_pk = self.digest.pk
            self.digest.delete()
        bump.assert_any_call("digest", digest_pk)
        self.assertEqual(
            Feed.objects.filter(feed_url=first_feed.feed_url).count(), 0
        )

    def test_deleting_digest_invalidates_digest_cache(self):
        digest_pk = self.digest.pk
        with (
            patch("core.models.filter.bump_cache_version") as bump,
            self.captureOnCommitCallbacks(execute=True),
        ):
            self.digest.delete()
        bump.assert_any_call("digest", digest_pk)


class BatchModifyCacheInvalidationTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser("admin", "a@b.c", "pw")
        self.feed = Feed.objects.create(
            name="F", feed_url="https://example.com/batch.xml", slug="batch-f"
        )

    def _post_batch(self, data):
        from core.actions import feed_batch_modify

        request = RequestFactory().post(
            "/",
            {
                "apply": "1",
                "_selected_action": [str(self.feed.pk)],
                "action": "feed_batch_modify",
                **data,
            },
        )
        request.user = self.user
        with patch("core.actions.bump_cache_version") as bump:
            feed_batch_modify(None, request, Feed.objects.filter(pk=self.feed.pk))
        return bump

    def test_batch_tag_reassignment_bumps_old_and_new_tags_only(self):
        old_tag = Tag.objects.create(name="batch-old")
        new_tag = Tag.objects.create(name="batch-new")
        self.feed.tags.add(old_tag)

        bump = self._post_batch({"tags": "Change", "tags_value": [str(new_tag.pk)]})
        calls = [c.args for c in bump.call_args_list]
        self.assertIn(("tag", old_tag.pk), calls)
        self.assertIn(("tag", new_tag.pk), calls)
        self.assertNotIn(("rss", self.feed.pk), calls)

    def test_batch_filter_reassignment_bumps_rss_and_opt_in_tags_only(self):
        filt = Filter.objects.create(name="batch-filter")
        opt_in = Tag.objects.create(name="batch-opt-in", apply_feed_filters=True)
        opt_out = Tag.objects.create(name="batch-opt-out", apply_feed_filters=False)
        self.feed.tags.add(opt_in, opt_out)

        bump = self._post_batch({"filter": "Change", "filter_value": [str(filt.pk)]})
        calls = [c.args for c in bump.call_args_list]
        self.assertIn(("rss", self.feed.pk), calls)
        self.assertIn(("tag", opt_in.pk), calls)
        self.assertNotIn(("tag", opt_out.pk), calls)


class TagForceUpdateTest(TestCase):
    """tag_force_update must bump the version or cache_tag serves the stale feed."""

    def test_action_bumps_tag_version(self):
        from core.actions import tag_force_update

        tag = Tag.objects.create(name="t1")
        with (
            patch("core.actions.task_manager.submit_task"),
            patch("core.actions.bump_cache_version") as bump,
        ):
            tag_force_update(None, None, Tag.objects.filter(pk=tag.pk))
        bump.assert_any_call("tag", tag.pk)


class DigestGenerationCacheTest(TestCase):
    """cache_digest() after generation must rebuild, not serve the stale feed."""

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.agent = OpenAIAgent.objects.create(name="agent", api_key="k")
        self.digest = Digest.objects.create(name="d", summarizer=self.agent)

    def tearDown(self):
        from django.core.cache import cache

        cache.clear()

    def _run_generate(self, *extra_patches):
        from contextlib import ExitStack
        from core.tasks import generate_digests

        def _fake_prepare(generator):
            generator.articles = [{"a": 1}]

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    self.digest.summarizer,
                    "digester",
                    return_value={"text": "digest body", "tokens": 1},
                )
            )
            stack.enter_context(
                patch.object(
                    generate_digests, "_ensure_entries_have_summaries", return_value=[]
                )
            )
            stack.enter_context(
                patch.object(
                    generate_digests.DigestGenerator,
                    "prepare_articles",
                    _fake_prepare,
                )
            )
            stack.enter_context(
                patch.object(
                    generate_digests.DigestGenerator,
                    "build_prompt",
                    return_value=(["articles text"], "prompt", {}),
                )
            )
            for ctx in extra_patches:
                stack.enter_context(ctx)
            return generate_digests.DigestGenerator(self.digest).generate()

    def test_generate_bumps_digest_version_before_warming_cache(self):
        from core.models.filter import cache_version

        version_before = cache_version("digest", self.digest.pk)
        result = self._run_generate(
            patch("core.cache.generate_atom_feed", return_value="<feed>fresh</feed>")
        )

        self.assertTrue(result["success"])
        self.assertGreater(cache_version("digest", self.digest.pk), version_before)

    def test_cache_warmup_failure_does_not_mark_generation_failed(self):
        from core.models.filter import cache_version
        from core.tasks import generate_digests

        version_before = cache_version("digest", self.digest.pk)
        result = self._run_generate(
            patch.object(
                generate_digests,
                "cache_digest",
                side_effect=RuntimeError("cache down"),
            )
        )

        self.assertTrue(result["success"])
        self.digest.refresh_from_db()
        self.assertTrue(self.digest.status)
        self.assertGreater(cache_version("digest", self.digest.pk), version_before)

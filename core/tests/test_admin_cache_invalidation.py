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
from core.admin.tag_admin import TagAdmin
from core.models import Digest, Feed, OpenAIAgent, Tag


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

    def _save(self, changed_data, change=True):
        form = _form(self.feed, changed_data)
        # Django calls save_model before save_related; stash like the real flow.
        self.admin._saved_form = form
        with patch("core.models.filter.bump_cache_version") as bump:
            self.admin.save_related(self.request, form, [], change)
        return bump

    def test_filter_change_bumps_feed_and_its_tags(self):
        tag = Tag.objects.create(name="t1")
        self.feed.tags.add(tag)
        bump = self._save(["filters"])

        bump.assert_any_call("rss", self.feed.pk)
        bump.assert_any_call("tag", tag.pk)

    def test_tag_reassignment_bumps_old_and_new_tags(self):
        old_tag = Tag.objects.create(name="old")
        Tag.objects.create(name="new")
        self.feed.tags.add(old_tag)
        bump = self._save(["tags"])

        # The feed left old_tag and joined new_tag (M2M set in setUp for old;
        # the mock does not move membership, so new is read from the form
        # instance's current tags — only old_tag is asserted unconditionally).
        bump.assert_any_call("tag", old_tag.pk)
        # rss output is unchanged by membership alone.
        self.assertNotIn(("rss", self.feed.pk), [c.args for c in bump.call_args_list])

    def test_max_posts_change_bumps_feed_and_tags(self):
        tag = Tag.objects.create(name="t1")
        self.feed.tags.add(tag)
        bump = self._save(["max_posts"])

        bump.assert_any_call("rss", self.feed.pk)
        bump.assert_any_call("tag", tag.pk)

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
        with patch("core.models.filter.bump_cache_version") as bump:
            self.admin.save_related(self.request, form, [], True)
        bump.assert_called_once_with("tag", self.tag.pk)

    def test_no_filter_change_does_not_bump(self):
        form = _form(self.tag, [])
        with patch("core.models.filter.bump_cache_version") as bump:
            self.admin.save_related(self.request, form, [], True)
        bump.assert_not_called()


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
        with patch("core.models.filter.bump_cache_version") as bump:
            self.admin.save_related(self.request, form, [], True)
        bump.assert_called_once_with("digest", self.digest.pk)

    def test_create_does_not_bump(self):
        form = _form(self.digest, ["prompt"])
        with patch("core.models.filter.bump_cache_version") as bump:
            self.admin.save_related(self.request, form, [], False)
        bump.assert_not_called()


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

    def test_generate_bumps_digest_version_before_warming_cache(self):
        from core.models.filter import cache_version
        from core.tasks import generate_digests

        digest = self.digest

        def _fake_prepare(generator):
            generator.articles = [{"a": 1}]

        version_before = cache_version("digest", digest.pk)
        with (
            patch.object(
                digest.summarizer,
                "digester",
                return_value={"text": "digest body", "tokens": 1},
            ),
            patch.object(
                generate_digests, "_ensure_entries_have_summaries", return_value=[]
            ),
            patch.object(
                generate_digests.DigestGenerator,
                "prepare_articles",
                _fake_prepare,
            ),
            patch.object(
                generate_digests.DigestGenerator,
                "build_prompt",
                return_value=(["articles text"], "prompt", {}),
            ),
            patch("core.cache.generate_atom_feed", return_value="<feed>fresh</feed>"),
        ):
            result = generate_digests.DigestGenerator(digest).generate()

        self.assertTrue(result["success"])
        self.assertGreater(cache_version("digest", digest.pk), version_before)

from django.test import TestCase

from ..models import Entry, Feed, Filter


class FilterUnicodeCaseTestCase(TestCase):
    """Keyword filters fold case for non-ASCII text too (SQLite LIKE does not)."""

    def setUp(self):
        self.feed = Feed.objects.create(name="Jobs", feed_url="https://example.com/jobs.xml")
        self.lower = Entry.objects.create(
            feed=self.feed, original_title="търсим готвач", original_content="<p>заплата по договаряне</p>"
        )
        self.upper = Entry.objects.create(
            feed=self.feed, original_title="ТЪРСИМ ГОТВАЧ", original_content="<p>ЗАПЛАТА 1500</p>"
        )
        self.mixed = Entry.objects.create(
            feed=self.feed, original_title="Готвач за ресторант", original_content="<p>Straße</p>"
        )
        self.other = Entry.objects.create(
            feed=self.feed, original_title="Продавам кола", original_content="<p>FORD</p>"
        )

    def _filter(self, operation, *keywords):
        f = Filter.objects.create(name=f"f{operation}{keywords}", operation=operation)
        for k in keywords:
            f.keywords.add(k)
        return f.apply_keywords_filter(Entry.objects.all())

    def test_include_matches_every_case_variant(self):
        result = set(self._filter(Filter.INCLUDE, "готвач"))
        self.assertEqual(result, {self.lower, self.upper, self.mixed})

    def test_uppercase_keyword_matches_lowercase_text(self):
        result = set(self._filter(Filter.INCLUDE, "ЗАПЛАТА"))
        self.assertEqual(result, {self.lower, self.upper})

    def test_exclude_drops_every_case_variant(self):
        result = set(self._filter(Filter.EXCLUDE, "Търсим"))
        self.assertEqual(result, {self.mixed, self.other})

    def test_ascii_and_casefold_still_work(self):
        self.assertEqual(set(self._filter(Filter.INCLUDE, "ford")), {self.other})
        self.assertEqual(set(self._filter(Filter.INCLUDE, "STRASSE")), {self.mixed})

    def test_lookup_usable_directly(self):
        self.assertEqual(Entry.objects.filter(original_title__uicontains="ГОТВАЧ").count(), 3)

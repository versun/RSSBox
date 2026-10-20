import os
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase

BASE_DIR = Path(__file__).resolve().parents[2]


class RssFetchTimeoutSettingsTests(SimpleTestCase):
    def _check(self, value):
        env = {**os.environ, "RSS_FETCH_TIMEOUT": value}
        return subprocess.run(
            [sys.executable, "manage.py", "check"],
            cwd=BASE_DIR,
            env=env,
            capture_output=True,
            text=True,
        )

    def test_rejects_non_positive_or_non_finite(self):
        for value in ("0", "-5", "nan", "inf", "-inf"):
            with self.subTest(value=value):
                result = self._check(value)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("RSS_FETCH_TIMEOUT must be", result.stderr)

    def test_accepts_finite_positive(self):
        for value in ("0.5", "30"):
            with self.subTest(value=value):
                self.assertEqual(self._check(value).returncode, 0)

from unittest.mock import patch

from django.test import SimpleTestCase

from scripts.dev_server import start_development_server


class DevServerScriptTests(SimpleTestCase):
    @patch("scripts.dev_server.print")
    @patch("scripts.dev_server.subprocess.run")
    @patch("scripts.dev_server.sys.executable", "C:\\venv\\Scripts\\python.exe")
    def test_start_development_server_uses_current_python(self, mock_run, mock_print):
        start_development_server()

        mock_run.assert_called_once_with(
            ["C:\\venv\\Scripts\\python.exe", "manage.py", "runserver"],
            check=True,
        )

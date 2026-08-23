import unittest
from pathlib import Path
from unittest.mock import patch

import platform_support


class DesktopIntegrationTests(unittest.TestCase):
    def test_macos_uses_open(self):
        target = str(Path("output").resolve())
        with patch("platform_support.platform.system", return_value="Darwin"), patch(
            "platform_support.subprocess.Popen"
        ) as popen:
            platform_support.open_in_file_manager("output")
        popen.assert_called_once_with(["open", target])

    def test_linux_uses_xdg_open(self):
        target = str(Path("output").resolve())
        with patch("platform_support.platform.system", return_value="Linux"), patch(
            "platform_support.subprocess.Popen"
        ) as popen:
            platform_support.open_in_file_manager("output")
        popen.assert_called_once_with(["xdg-open", target])


if __name__ == "__main__":
    unittest.main()

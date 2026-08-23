import os
import unittest
from unittest.mock import patch

import i18n
from runtime_policy import DataScale, format_data_scale


class LanguageTests(unittest.TestCase):
    def setUp(self):
        self.original_preference = i18n.get_language_preference()

    def tearDown(self):
        i18n.set_language(self.original_preference)

    def test_normalizes_supported_language_names(self):
        self.assertEqual(i18n.normalize_language("en-US"), "en")
        self.assertEqual(i18n.normalize_language("zh-CN"), "zh_CN")
        self.assertEqual(i18n.normalize_language("system", resolve_auto=False), "auto")

    def test_auto_language_uses_environment(self):
        with patch.dict(os.environ, {"AMREX_VIEWER_LANG": "zh-TW"}, clear=False):
            self.assertEqual(i18n.detect_system_language(), "zh_CN")

    def test_switching_back_to_auto_redetects_the_system_language(self):
        with patch.dict(os.environ, {"LANG": "zh_CN.UTF-8"}, clear=True):
            i18n.set_language("en")
            self.assertEqual(i18n.set_language("auto"), "zh_CN")

    def test_translation_and_formatting(self):
        i18n.set_language("zh_CN")
        self.assertEqual(i18n.tr("Ready"), "就绪")
        self.assertEqual(i18n.tr("Elapsed {duration}", duration="00:05"), "用时 00:05")
        self.assertEqual(i18n.tr_for("en", "Ready"), "Ready")

    def test_data_scale_summary_uses_selected_language(self):
        scale = DataScale(
            file_count=2,
            total_bytes=2 * 1024 * 1024,
            timestep_count=4,
            level_count=2,
            variable_count=3,
            dimension=3,
            estimated_timestep_bytes=512 * 1024,
            storage_kind="SSD",
        )
        summary = format_data_scale(scale, language="zh_CN")
        self.assertIn("2 个文件", summary)
        self.assertIn("3D", summary)


if __name__ == "__main__":
    unittest.main()

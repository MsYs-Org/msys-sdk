from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from msys_sdk import PackageI18n


class PackageI18nTests(unittest.TestCase):
    def test_package_catalog_and_recovery_fallback_are_app_owned(self) -> None:
        fallback = {"title": "Fallback", "count": "Count {value}"}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "catalog.json"
            path.write_text(json.dumps({
                "schema": "msys.i18n.catalog.v1",
                "id": "org.example.app",
                "default_locale": "en-US",
                "messages": {
                    "en-US": {"title": "Title", "count": "Count {value}"},
                    "zh-CN": {"title": "标题", "count": "数量 {value}"},
                },
            }, ensure_ascii=False), encoding="utf-8")
            i18n = PackageI18n(path, fallback, locale="zh-CN")
            self.assertEqual(i18n("title"), "标题")
            self.assertEqual(i18n("count", {"value": 2}), "数量 2")

            broken = PackageI18n(path.with_name("missing.json"), fallback)
            self.assertTrue(broken.load_error)
            self.assertEqual(broken("count", {"value": 3}), "Count 3")


if __name__ == "__main__":
    unittest.main()

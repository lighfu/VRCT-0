"""utils/update_version.py (版をリリースした日付にする) のテスト。

pytest の pythonpath は src-python で、そこにも utils.py があるため、
リポジトリ直下の utils/update_version.py はファイルの場所から読み込む。
"""

import datetime
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("update_version", ROOT / "utils" / "update_version.py")
update_version = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(update_version)

_PACKAGE_JSON = """{
  "name": "vrct",
  "private": true,
  "version": "2026.9.24",
  "scripts": {
    "x": "y"
  },
  "dependencies": {
    "react": "18.3.1"
  }
}
"""

_PACKAGE_LOCK = """{
  "name": "vrct",
  "version": "2026.9.24",
  "lockfileVersion": 3,
  "packages": {
    "": {
      "name": "vrct",
      "version": "2026.9.24",
      "dependencies": {}
    },
    "node_modules/react": {
      "version": "18.3.1"
    }
  }
}
"""

_TAURI_CONF = {"productName": "VRCT-0", "version": "2026.9.24"}
_CONFIG_PY = 'class Config:\n    def init(self):\n        self._VERSION = "2026.9.24"\n'


class DateVersionTests(unittest.TestCase):
    def test_formats_without_zero_padding(self) -> None:
        self.assertEqual(update_version.dateVersion(datetime.date(2026, 9, 5)), "2026.9.5")
        self.assertEqual(update_version.dateVersion(datetime.date(2026, 12, 31), beta=2), "2026.12.31-beta.2")

    def test_accepts_only_real_dates(self) -> None:
        for version in ("2026.9.25", "2026.9.25-beta.1", "2026.12.31-rc.3", "2028.2.29"):
            with self.subTest(version=version):
                self.assertTrue(update_version.isDateVersion(version))
        for version in (
            "2026.09.25",        # ゼロで埋めると SemVer にならない
            "3.5.1-beta.1",      # 日付ではない
            "2026.2.30",         # 無い日
            "2026.13.1",
            "2026.9.25-beta.0",
            "2026.9.25-alpha.1",
            "2026.9",
        ):
            with self.subTest(version=version):
                self.assertFalse(update_version.isDateVersion(version))


class WriteVersionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "src-tauri").mkdir()
        (root / "src-python").mkdir()
        (root / "package.json").write_text(_PACKAGE_JSON, encoding="utf-8")
        (root / "package-lock.json").write_text(_PACKAGE_LOCK, encoding="utf-8")
        (root / "src-tauri" / "tauri.conf.json").write_text(json.dumps(_TAURI_CONF, indent=4), encoding="utf-8")
        (root / "src-python" / "config.py").write_text(_CONFIG_PY, encoding="utf-8")
        self.root = root
        patcher = mock.patch.object(update_version, "ROOT", str(root))
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_date_option_sets_every_file(self) -> None:
        update_version.main(["--date", "2026-09-25", "--beta", "1"])
        self.assertEqual(json.loads((self.root / "package.json").read_text(encoding="utf-8"))["version"], "2026.9.25-beta.1")
        lock = json.loads((self.root / "package-lock.json").read_text(encoding="utf-8"))
        self.assertEqual(lock["version"], "2026.9.25-beta.1")
        self.assertEqual(lock["packages"][""]["version"], "2026.9.25-beta.1")
        # 依存の版は書き換えない。
        self.assertEqual(lock["packages"]["node_modules/react"]["version"], "18.3.1")
        self.assertEqual(json.loads((self.root / "package.json").read_text(encoding="utf-8"))["dependencies"]["react"], "18.3.1")
        tauri = json.loads((self.root / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
        self.assertEqual(tauri["version"], "2026.9.25-beta.1")
        self.assertIn('self._VERSION = "2026.9.25-beta.1"', (self.root / "src-python" / "config.py").read_text(encoding="utf-8"))

    def test_package_json_keeps_its_formatting(self) -> None:
        update_version.main(["--date", "2026-09-25"])
        self.assertEqual(
            (self.root / "package.json").read_text(encoding="utf-8"),
            _PACKAGE_JSON.replace('"version": "2026.9.24"', '"version": "2026.9.25"'),
        )

    def test_date_defaults_to_today(self) -> None:
        with mock.patch.object(update_version.datetime, "date", wraps=datetime.date) as date:
            date.today.return_value = datetime.date(2026, 10, 1)
            update_version.main(["--date"])
        self.assertEqual(json.loads((self.root / "package.json").read_text(encoding="utf-8"))["version"], "2026.10.1")

    def test_sync_refuses_a_non_date_version(self) -> None:
        text = _PACKAGE_JSON.replace('"version": "2026.9.24"', '"version": "3.5.1-beta.1"')
        (self.root / "package.json").write_text(text, encoding="utf-8")
        with self.assertRaises(SystemExit):
            update_version.main([])
        # 何も書き換えない。
        self.assertIn('"2026.9.24"', (self.root / "src-python" / "config.py").read_text(encoding="utf-8"))

    def test_beta_needs_date(self) -> None:
        with self.assertRaises(SystemExit):
            update_version.main(["--beta", "1"])


if __name__ == "__main__":
    unittest.main()

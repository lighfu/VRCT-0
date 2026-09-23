"""tools/measure_footprint.py の純関数のテスト。

サイドカーの起動を伴う計測本体は実機でしか意味が無いので、
ここでは集計・判定・パースだけを確かめる。
"""

import importlib.util
import json
import os
import tempfile
import unittest

_TOOL_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "tools", "measure_footprint.py")
_spec = importlib.util.spec_from_file_location("measure_footprint", _TOOL_PATH)
measure_footprint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(measure_footprint)


class SummarizeDirectoryTests(unittest.TestCase):
    def _write(self, root: str, relative: str, size: int) -> None:
        path = os.path.join(root, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"x" * size)

    def test_totals_and_ranks_top_level_entries(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            self._write(root, "_internal/big/a.bin", 300)
            self._write(root, "_internal/small/b.bin", 10)
            self._write(root, "sidecar.exe", 50)
            summary = measure_footprint.summarizeDirectory(root, top=20)
        self.assertEqual(summary["total_bytes"], 360)
        self.assertEqual(summary["top_entries"][0], {"name": "_internal", "bytes": 310})
        self.assertEqual(summary["top_entries"][1], {"name": "sidecar.exe", "bytes": 50})

    def test_ranks_second_level_entries_under_internal(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            self._write(root, "_internal/big/a.bin", 300)
            self._write(root, "_internal/small/b.bin", 10)
            summary = measure_footprint.summarizeDirectory(root, top=20)
        self.assertEqual(summary["internal_top_entries"][0], {"name": "big", "bytes": 300})

    def test_excludes_runtime_generated_entries(self) -> None:
        # 初回起動でサイドカーが作る weights/ logs/ config.json は配布物ではない。
        with tempfile.TemporaryDirectory() as root:
            self._write(root, "sidecar.exe", 50)
            self._write(root, "weights/whisper/base/model.bin", 1000)
            self._write(root, "logs/process.log", 5)
            self._write(root, "config.json", 5)
            summary = measure_footprint.summarizeDirectory(root, top=20)
        self.assertEqual(summary["total_bytes"], 50)


class IsInitializationCompleteTests(unittest.TestCase):
    def test_true_for_initialization_complete_response(self) -> None:
        line = json.dumps({"status": 200, "endpoint": "/run/initialization_complete", "result": {}})
        self.assertTrue(measure_footprint.isInitializationComplete(line))

    def test_false_for_other_endpoints_logs_and_garbage(self) -> None:
        self.assertFalse(measure_footprint.isInitializationComplete(
            json.dumps({"status": 200, "endpoint": "/run/initialization_progress", "result": 1})))
        self.assertFalse(measure_footprint.isInitializationComplete(
            json.dumps({"status": 348, "log": "/run/initialization_complete", "data": ""})))
        self.assertFalse(measure_footprint.isInitializationComplete("not json"))


class ParseImportTimeTests(unittest.TestCase):
    def test_returns_top_modules_by_cumulative_time(self) -> None:
        stderr_text = "\n".join([
            "import time: self [us] | cumulative | imported package",
            "import time:       100 |        100 |   small",
            "import time:      2000 |     500000 | transformers",
            "import time:        50 |      90000 |   ctranslate2",
            "unrelated line",
        ])
        top = measure_footprint.parseImportTime(stderr_text, top=2)
        self.assertEqual(top, [
            {"module": "transformers", "cumulative_us": 500000},
            {"module": "ctranslate2", "cumulative_us": 90000},
        ])


if __name__ == "__main__":
    unittest.main()

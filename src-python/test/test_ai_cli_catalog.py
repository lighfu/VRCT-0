import json
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import psutil

from models.translation.ai_cli import catalog


def _which(found):
    return lambda name: {"codex.cmd": "C:/npm/codex.cmd", "claude": "C:/bin/claude.exe", "agy": "C:/bin/agy.exe"}.get(name) if name in found else None


class ResolveTests(unittest.TestCase):
    def test_codex_prefers_cmd_shim(self):
        with patch.object(catalog.shutil, "which", side_effect=_which({"codex.cmd"})):
            self.assertEqual(catalog.resolveExecutable("codex"), "C:/npm/codex.cmd")

    def test_detect_keeps_fixed_order(self):
        with patch.object(catalog.shutil, "which", side_effect=_which({"agy", "codex.cmd"})):
            self.assertEqual(catalog.detectInstalledTools(), ["codex", "agy"])

    def test_unknown_tool_resolves_to_none(self):
        self.assertIsNone(catalog.resolveExecutable("notepad"))


class ListModelsTests(unittest.TestCase):
    def test_claude_is_a_fixed_list(self):
        self.assertEqual(catalog.listModels("claude"), ["haiku", "sonnet", "opus"])

    def test_codex_reads_debug_models_and_drops_review(self):
        payload = json.dumps({"models": [{"slug": "gpt-5.5"}, {"slug": "codex-auto-review"}, {"slug": "gpt-6-sol"}]})
        with patch.object(catalog, "resolveExecutable", return_value="codex.cmd"), \
             patch.object(catalog, "_run", return_value=payload) as mock_run:
            self.assertEqual(catalog.listModels("codex"), ["gpt-5.5", "gpt-6-sol"])
        self.assertEqual(mock_run.call_args.args, ("codex.cmd", ["debug", "models"]))

    def test_agy_reads_tab_separated_lines(self):
        stdout = "Fetching available models...\ngemini-3.8-flash-low\tGemini 3.8 Flash (Low)\nclaude-sonnet-4-6\tClaude Sonnet 4.6\n"
        with patch.object(catalog, "resolveExecutable", return_value="agy.exe"), \
             patch.object(catalog, "_run", return_value=stdout):
            self.assertEqual(catalog.listModels("agy"), ["gemini-3.8-flash-low", "claude-sonnet-4-6"])

    def test_failure_returns_empty_list_and_logs(self):
        for tool in ("codex", "agy"):
            with self.subTest(tool=tool), \
                 patch.object(catalog, "resolveExecutable", return_value=f"{tool}.exe"), \
                 patch.object(catalog, "_run", side_effect=RuntimeError("Command failed with exit code 1: not logged in")), \
                 patch.object(catalog, "errorLogging") as mock_error_log:
                self.assertEqual(catalog.listModels(tool), [])
                mock_error_log.assert_called_once()

    def test_missing_cli_returns_empty_list(self):
        with patch.object(catalog, "resolveExecutable", return_value=None):
            self.assertEqual(catalog.listModels("codex"), [])


class RunTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_returns_stdout(self):
        self.assertEqual(catalog._run(sys.executable, ["-c", "print('a\\tb')"]).strip(), "a\tb")

    def test_non_zero_exit_raises_with_the_output(self):
        with self.assertRaises(RuntimeError) as caught:
            catalog._run(sys.executable, ["-c", "import sys; sys.stderr.write('not logged in'); sys.exit(1)"])
        self.assertIn("not logged in", str(caught.exception))

    @unittest.skipUnless(os.name == "nt", "codex.cmd のような .cmd シムは Windows だけ")
    def test_timeout_kills_the_whole_tree_of_a_cmd_shim(self):
        # subprocess.run(timeout=...) は cmd.exe だけを殺し、パイプを握った孫プロセスの
        # 終了まで待ってしまう (実測: 2 秒の締め切りで 12.5 秒)。木ごと止めて締め切りを守る。
        pid_file = os.path.join(self._tmp.name, "sleeper.pid")
        sleeper = os.path.join(self._tmp.name, "sleeper.py")
        with open(sleeper, "w", encoding="utf-8") as f:
            f.write("import os, sys, time\n"
                    "open(sys.argv[1], 'w').write(str(os.getpid()))\n"
                    "time.sleep(60)\n")
        shim = os.path.join(self._tmp.name, "slow.cmd")
        with open(shim, "w", encoding="utf-8") as f:
            f.write(f'@"{sys.executable}" "{sleeper}" "{pid_file}"\r\n')
        start = time.monotonic()
        with self.assertRaises(RuntimeError):
            catalog._run(shim, [], timeout=2)
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 10)
        with open(pid_file, encoding="utf-8") as f:
            sleeper_pid = int(f.read())
        deadline = time.monotonic() + 5
        while psutil.pid_exists(sleeper_pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        alive = False
        if psutil.pid_exists(sleeper_pid):
            try:
                alive = psutil.Process(sleeper_pid).status() != psutil.STATUS_ZOMBIE
            except psutil.NoSuchProcess:
                alive = False
        self.assertFalse(alive, "the grandchild of the .cmd shim is still running")


if __name__ == "__main__":
    unittest.main()

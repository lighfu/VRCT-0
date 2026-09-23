import json
import subprocess
import unittest
from unittest.mock import patch

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
    def _run(self, stdout):
        return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")

    def test_claude_is_a_fixed_list(self):
        self.assertEqual(catalog.listModels("claude"), ["haiku", "sonnet", "opus"])

    def test_codex_reads_debug_models_and_drops_review(self):
        payload = json.dumps({"models": [{"slug": "gpt-5.5"}, {"slug": "codex-auto-review"}, {"slug": "gpt-6-sol"}]})
        with patch.object(catalog, "resolveExecutable", return_value="codex.cmd"), \
             patch.object(catalog.subprocess, "run", return_value=self._run(payload)) as mock_run:
            self.assertEqual(catalog.listModels("codex"), ["gpt-5.5", "gpt-6-sol"])
        self.assertEqual(mock_run.call_args.args[0][1:], ["debug", "models"])

    def test_agy_reads_tab_separated_lines(self):
        stdout = "Fetching available models...\ngemini-3.8-flash-low\tGemini 3.8 Flash (Low)\nclaude-sonnet-4-6\tClaude Sonnet 4.6\n"
        with patch.object(catalog, "resolveExecutable", return_value="agy.exe"), \
             patch.object(catalog.subprocess, "run", return_value=self._run(stdout)):
            self.assertEqual(catalog.listModels("agy"), ["gemini-3.8-flash-low", "claude-sonnet-4-6"])

    def test_failure_returns_empty_list(self):
        with patch.object(catalog, "resolveExecutable", return_value="agy.exe"), \
             patch.object(catalog.subprocess, "run", side_effect=subprocess.TimeoutExpired("agy", 30)):
            self.assertEqual(catalog.listModels("agy"), [])

    def test_missing_cli_returns_empty_list(self):
        with patch.object(catalog, "resolveExecutable", return_value=None):
            self.assertEqual(catalog.listModels("codex"), [])

    def test_codex_non_zero_exit_returns_empty_and_logs(self):
        with patch.object(catalog, "resolveExecutable", return_value="codex.cmd"), \
             patch.object(catalog.subprocess, "run", return_value=subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="Error: not logged in")), \
             patch.object(catalog, "errorLogging") as mock_error_log:
            self.assertEqual(catalog.listModels("codex"), [])
            mock_error_log.assert_called_once()

    def test_agy_non_zero_exit_with_tab_output_returns_empty_and_logs(self):
        with patch.object(catalog, "resolveExecutable", return_value="agy.exe"), \
             patch.object(catalog.subprocess, "run", return_value=subprocess.CompletedProcess(args=[], returncode=1, stdout="model-id\tDescription", stderr="Error")), \
             patch.object(catalog, "errorLogging") as mock_error_log:
            self.assertEqual(catalog.listModels("agy"), [])
            mock_error_log.assert_called_once()


if __name__ == "__main__":
    unittest.main()

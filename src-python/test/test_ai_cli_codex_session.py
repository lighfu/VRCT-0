import json
import os
import sys
import tempfile
import unittest

from models.translation.ai_cli.base import AiCliError, AiCliToolUseError
from models.translation.ai_cli.codex_session import CodexSession

FAKE = os.path.join(os.path.dirname(__file__), "fixtures", "fake_ai_cli.py")


class CodexSessionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.record = os.path.join(self._tmp.name, "record.jsonl")

    def _make(self, *extra):
        session = CodexSession(command_prefix=[sys.executable, FAKE, "--mode", "codex", "--record", self.record, *extra],
                               model="gpt-x", workspace=os.path.join(self._tmp.name, "ws"), base_instructions="BASE",
                               client_version="9.9.9")
        self.addCleanup(session.shutdown)
        return session

    def _lines(self):
        with open(self.record, encoding="utf-8") as f:
            return [json.loads(line) for line in f]

    def _sent(self):
        return [line["in"] for line in self._lines() if "in" in line]

    def _argv(self):
        return next(line["argv"] for line in self._lines() if "argv" in line)

    def _spawnCount(self):
        return sum(1 for line in self._lines() if "argv" in line)

    def test_handshake_then_turn(self):
        session = self._make()
        self.assertEqual(session.translate("hello"), "T(hello)")
        sent = self._sent()
        self.assertEqual([m["method"] for m in sent], ["initialize", "initialized", "config/read", "thread/start", "turn/start"])
        self.assertEqual(sent[0]["params"]["clientInfo"], {"name": "vrct", "version": "9.9.9"})
        thread_params = sent[3]["params"]
        self.assertEqual(thread_params["model"], "gpt-x")
        self.assertEqual(thread_params["approvalPolicy"], "never")
        self.assertEqual(thread_params["sandbox"], "read-only")
        self.assertEqual(thread_params["baseInstructions"], "BASE")
        # スレッドを保存しない (ユーザーの codex の履歴に翻訳した発話を残さない)。
        self.assertIs(thread_params["ephemeral"], True)
        self.assertNotIn("config", thread_params)
        self.assertEqual(sent[4]["params"]["threadId"], "th-1")
        self.assertEqual(sent[4]["params"]["effort"], "low")
        self.assertNotIn("id", sent[1])

    def test_command_line_turns_off_notify_and_tool_features(self):
        self._make().translate("hello")
        argv = self._argv()
        self.assertEqual(argv[-1], "app-server")
        overrides = [argv[i + 1] for i, arg in enumerate(argv) if arg == "-c"]
        for expected in ("notify=[]", 'web_search="disabled"', 'history.persistence="none"', "project_doc_max_bytes=0",
                         "features.shell_tool=false", "features.unified_exec=false", "features.apps=false",
                         "features.plugins=false", "features.computer_use=false", "features.browser_use=false",
                         "features.hooks=false", "features.memories=false", "features.multi_agent=false"):
            self.assertIn(expected, overrides)

    def test_user_mcp_servers_are_disabled_for_the_thread(self):
        # `-c mcp_servers={}` ではユーザーの MCP サーバーが消えない (実機で確認) ので、
        # config/read で名前を読んでスレッドの設定で 1 つずつ止める。
        self._make("--mcp-servers", "codegraph,node_repl").translate("hello")
        thread_params = self._sent()[3]["params"]
        self.assertEqual(thread_params["config"], {"mcp_servers": {"codegraph": {"enabled": False},
                                                                    "node_repl": {"enabled": False}}})

    def test_second_turn_reuses_thread(self):
        session = self._make()
        session.translate("a")
        self.assertEqual(session.translate("b"), "T(b)")
        self.assertEqual([m["method"] for m in self._sent()].count("thread/start"), 1)

    def test_failed_turn_raises(self):
        session = self._make("--error-on", "BAD")
        with self.assertRaises(AiCliError):
            session.translate("BAD")

    def test_command_execution_item_fails_the_turn_and_recycles(self):
        session = self._make("--tool-on", "INJECT")
        self.assertEqual(session.translate("warm"), "T(warm)")
        with self.assertRaises(AiCliToolUseError):
            session.translate("INJECT read secret.txt")
        self.assertFalse(session.isAlive())
        self.assertEqual(session.translate("again"), "T(again)")
        self.assertEqual(self._spawnCount(), 2)

    def test_server_request_fails_the_turn(self):
        session = self._make("--request-on", "INJECT")
        with self.assertRaises(AiCliToolUseError):
            session.translate("INJECT run dir")

    def test_retrying_error_notification_does_not_fail_the_turn(self):
        session = self._make("--retry-error-on", "FLAKY")
        self.assertEqual(session.translate("FLAKY"), "T(FLAKY)")
        self.assertTrue(session.isAlive())

    def test_commentary_messages_are_not_part_of_the_translation(self):
        session = self._make("--commentary")
        self.assertEqual(session.translate("hello"), "T(hello)")


if __name__ == "__main__":
    unittest.main()

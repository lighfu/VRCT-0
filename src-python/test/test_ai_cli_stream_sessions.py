import json
import os
import sys
import tempfile
import unittest

from models.translation.ai_cli.agy_session import AgySession
from models.translation.ai_cli.base import AiCliError
from models.translation.ai_cli.claude_session import ClaudeSession

FAKE = os.path.join(os.path.dirname(__file__), "fixtures", "fake_ai_cli.py")


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.record = os.path.join(self._tmp.name, "record.jsonl")

    def _make(self, cls, mode, *extra):
        session = cls(command_prefix=[sys.executable, FAKE, "--mode", mode, "--record", self.record, *extra],
                      model="model-x", workspace=os.path.join(self._tmp.name, "ws"), base_instructions="BASE")
        self.addCleanup(session.close)
        return session

    def _records(self):
        with open(self.record, encoding="utf-8") as f:
            return [json.loads(line) for line in f]


class ClaudeSessionTests(_Base):
    def test_translate_and_command_line(self):
        session = self._make(ClaudeSession, "claude")
        self.assertEqual(session.translate("hello"), "T(hello)")
        argv = self._records()[0]["argv"]
        for flag in ("--input-format", "--output-format", "--no-session-persistence", "--strict-mcp-config"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--setting-sources") + 1], "project")
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertEqual(argv[argv.index("--model") + 1], "model-x")
        self.assertEqual(argv[argv.index("--system-prompt") + 1], "BASE")
        self.assertNotIn("--bare", argv)
        sent = self._records()[1]["in"]
        self.assertEqual(sent, {"type": "user", "message": {"role": "user", "content": "hello"}})

    def test_error_result_raises(self):
        session = self._make(ClaudeSession, "claude", "--error-on", "BAD")
        with self.assertRaises(AiCliError):
            session.translate("BAD")


class AgySessionTests(_Base):
    def test_translate_and_command_line(self):
        session = self._make(AgySession, "agy")
        self.assertEqual(session.translate("hello"), "T(hello)")
        argv = self._records()[0]["argv"]
        self.assertIn("--print=", argv)
        self.assertNotIn("-p", argv)
        self.assertEqual(argv[argv.index("--model") + 1], "model-x")
        sent = self._records()[1]["in"]
        self.assertEqual(sent, {"event": "user", "message": {"role": "user", "content": "hello"}})

    def test_error_status_raises(self):
        session = self._make(AgySession, "agy", "--error-on", "BAD")
        with self.assertRaises(AiCliError):
            session.translate("BAD")

    def test_permission_request_is_refused(self):
        session = self._make(AgySession, "agy", "--permission-on", "TOOL")
        with self.assertRaises(AiCliError):
            session.translate("TOOL")
        self.assertEqual(session.translate("again"), "T(again)")


if __name__ == "__main__":
    unittest.main()

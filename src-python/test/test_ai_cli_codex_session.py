import json
import os
import sys
import tempfile
import unittest

from models.translation.ai_cli.base import AiCliError
from models.translation.ai_cli.codex_session import CodexSession

FAKE = os.path.join(os.path.dirname(__file__), "fixtures", "fake_ai_cli.py")


class CodexSessionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.record = os.path.join(self._tmp.name, "record.jsonl")

    def _make(self, *extra):
        session = CodexSession(command_prefix=[sys.executable, FAKE, "--mode", "codex", "--record", self.record, *extra],
                               model="gpt-x", workspace=os.path.join(self._tmp.name, "ws"), base_instructions="BASE")
        self.addCleanup(session.close)
        return session

    def _sent(self):
        with open(self.record, encoding="utf-8") as f:
            return [json.loads(line)["in"] for line in f if '"in"' in line]

    def test_handshake_then_turn(self):
        session = self._make()
        self.assertEqual(session.translate("hello"), "T(hello)")
        sent = self._sent()
        self.assertEqual([m["method"] for m in sent], ["initialize", "initialized", "thread/start", "turn/start"])
        thread_params = sent[2]["params"]
        self.assertEqual(thread_params["model"], "gpt-x")
        self.assertEqual(thread_params["approvalPolicy"], "never")
        self.assertEqual(thread_params["sandbox"], "read-only")
        self.assertEqual(thread_params["baseInstructions"], "BASE")
        self.assertEqual(sent[3]["params"]["threadId"], "th-1")
        self.assertEqual(sent[3]["params"]["effort"], "low")
        self.assertNotIn("id", sent[1])

    def test_second_turn_reuses_thread(self):
        session = self._make()
        session.translate("a")
        self.assertEqual(session.translate("b"), "T(b)")
        self.assertEqual([m["method"] for m in self._sent()].count("thread/start"), 1)

    def test_failed_turn_raises(self):
        session = self._make("--error-on", "BAD")
        with self.assertRaises(AiCliError):
            session.translate("BAD")


if __name__ == "__main__":
    unittest.main()

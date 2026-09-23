"""CliSession（常駐セッションの共通基底）のテスト。偽 CLI を実際に起動する。"""

import os
import sys
import tempfile
import threading
import time
import unittest

from models.translation.ai_cli.base import AiCliError, CliSession

FAKE = os.path.join(os.path.dirname(__file__), "fixtures", "fake_ai_cli.py")


class _EchoSession(CliSession):
    """偽 CLI の claude モードを話す最小のサブクラス。"""

    def _buildArgs(self):
        return ["--mode", "claude"]

    def _turnMessages(self, prompt):
        return [{"type": "user", "message": {"role": "user", "content": prompt}}]

    def _interpret(self, message):
        if message.get("type") != "result":
            return None
        if message.get("is_error"):
            return ("error", str(message.get("result")))
        return ("ok", str(message.get("result")))


class _RaceSession(_EchoSession):
    """_spawn が Popen 完了後・公開前に close() と競合するのを決定的に再現する。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.raced_proc = None
        self._raced = False

    def _beforePublish(self, proc):
        if not self._raced:
            self._raced = True
            self.raced_proc = proc
            self.close()


class CliSessionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.record = os.path.join(self._tmp.name, "record.jsonl")
        self.workspace = os.path.join(self._tmp.name, "ws")

    def _session(self, *extra):
        session = _EchoSession(
            command_prefix=[sys.executable, FAKE, "--record", self.record, *extra],
            model="m", workspace=self.workspace, base_instructions="B",
        )
        self.addCleanup(session.close)
        return session

    def _spawnCount(self):
        with open(self.record, encoding="utf-8") as f:
            return sum(1 for line in f if '"argv"' in line)

    def test_translate_returns_answer_and_creates_workspace(self):
        session = self._session()
        self.assertEqual(session.translate("line1\nhello"), "T(hello)")
        self.assertTrue(os.path.isdir(self.workspace))
        self.assertTrue(session.isAlive())

    def test_multiple_turns_use_one_process(self):
        session = self._session()
        self.assertEqual(session.translate("a"), "T(a)")
        self.assertEqual(session.translate("b"), "T(b)")
        self.assertEqual(self._spawnCount(), 1)

    def test_session_is_recreated_after_max_turns(self):
        session = self._session()
        session.MAX_TURNS = 2
        for text in ("a", "b", "c"):
            session.translate(text)
        self.assertEqual(self._spawnCount(), 2)

    def test_turn_timeout_raises_and_next_turn_restarts(self):
        session = self._session("--hang-on", "SLOW")
        session.translate("warm")
        with self.assertRaises(AiCliError):
            session.translate("SLOW", timeout=1)
        self.assertFalse(session.isAlive())
        self.assertEqual(session.translate("again"), "T(again)")
        self.assertEqual(self._spawnCount(), 2)

    def test_process_exit_raises_and_next_turn_restarts(self):
        session = self._session("--exit-on", "DIE")
        session.translate("warm")
        with self.assertRaises(AiCliError):
            session.translate("DIE")
        self.assertEqual(session.translate("again"), "T(again)")

    def test_error_result_raises(self):
        session = self._session("--error-on", "BAD")
        with self.assertRaises(AiCliError):
            session.translate("BAD")

    def test_missing_executable_raises_ai_cli_error(self):
        session = _EchoSession(command_prefix=[os.path.join(self._tmp.name, "no_such_cli.exe")],
                               model="m", workspace=self.workspace, base_instructions="B")
        with self.assertRaises(AiCliError):
            session.translate("x")

    def test_close_is_idempotent(self):
        session = self._session()
        session.translate("a")
        session.close()
        session.close()
        self.assertFalse(session.isAlive())

    def test_close_from_another_thread_during_hung_turn_returns_quickly(self):
        session = self._session("--hang-on", "SLOW")
        session.translate("warm")
        proc = session._proc
        result = {}

        def hang():
            try:
                session.translate("SLOW", timeout=30)
            except AiCliError as e:
                result["error"] = e

        t = threading.Thread(target=hang)
        t.start()
        time.sleep(0.3)

        start = time.monotonic()
        session.close()
        elapsed = time.monotonic() - start

        t.join(timeout=35)
        self.assertLess(elapsed, 2.0)
        self.assertIn("error", result)
        self.assertIsNotNone(proc.poll())

    def test_write_watchdog_kills_stuck_stdin_write(self):
        session = self._session("--no-read")
        session.START_TIMEOUT = 1.0
        result = {}

        def run():
            try:
                result["value"] = session.translate("x" * 256_000, timeout=1)
            except AiCliError as e:
                result["error"] = e

        t = threading.Thread(target=run, daemon=True)
        start = time.monotonic()
        t.start()
        t.join(timeout=10)
        elapsed = time.monotonic() - start

        # 見張り役が退行してブロックし続けていたら、スレッドは 10 秒経っても
        # 生きたままになる (ハングではなく確実な失敗として検出する)。
        self.assertFalse(t.is_alive(), "translate() did not return in time; the watchdog likely regressed")
        self.assertLess(elapsed, 4.0)
        self.assertIn("error", result)
        self.assertFalse(session.isAlive())

    def test_error_result_then_next_turn_recovers(self):
        session = self._session("--error-on", "BAD")
        with self.assertRaises(AiCliError):
            session.translate("BAD")
        self.assertEqual(session.translate("again"), "T(again)")

    def test_shutdown_is_terminal(self):
        session = self._session()
        session.translate("a")
        session.shutdown()
        self.assertFalse(session.isAlive())
        with self.assertRaises(AiCliError):
            session.translate("b")
        self.assertEqual(self._spawnCount(), 1)

    def test_close_racing_spawn_kills_new_process_and_raises(self):
        session = _RaceSession(
            command_prefix=[sys.executable, FAKE, "--record", self.record],
            model="m", workspace=self.workspace, base_instructions="B",
        )
        self.addCleanup(session.close)

        with self.assertRaises(AiCliError):
            session.translate("x")

        self.assertFalse(session.isAlive())
        self.assertIsNotNone(session.raced_proc)
        # _spawn は公開前に close() との競合を検知して、公開できなかった
        # プロセスを自分で殺さなければならない。
        self.assertIsNotNone(session.raced_proc.wait(timeout=5))

        # 競合した 1 回だけで、以後は普通に起動・翻訳できる。
        self.assertEqual(session.translate("again"), "T(again)")
        self.assertEqual(self._spawnCount(), 2)


if __name__ == "__main__":
    unittest.main()

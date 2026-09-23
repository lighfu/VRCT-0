# AI CLI 翻訳プロバイダー 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ユーザーの PC に入っている codex / claude / agy の CLI を常駐させて翻訳に使う翻訳エンジン「AI CLI」を追加し、CLI とモデルを設定で選べるようにする。

**Architecture:** `models/translation/ai_cli/` に CLI ごとの常駐アダプター（共通基底 `CliSession`）と、導入済み CLI・モデル一覧の検出（`catalog`）を置く。翻訳エンジンとしての入口 `AICliClient` を既存の Ollama と同じ「疎通確認型」（`CONNECTION_PROVIDER_REGISTRY`）で `Translator` / `model` / `controller` / `mainloop` / UI に配線する。

**Tech Stack:** Python 3.11（subprocess・threading・queue）、既存の VRCT 設定基盤（`ManagedProperty`）、React（設定宣言からの自動生成）、unittest。

**Spec:** `docs/superpowers/specs/2026-09-24-ai-cli-translation-design.md`

## Global Constraints

- コード調査は CodeGraph（`codegraph_*`）から始める。grep/Read は文字列検索か既知ファイルに限る。
- エンジンキーは `AI_CLI`、CLI 名は `codex` / `claude` / `agy`（この順）。
- 常駐の上限: 1 セッション 50 ターンで作り直す。起動を含む最初のターンは 120 秒、以降は 60 秒でタイムアウト。
- claude のコマンド: `-p --input-format stream-json --output-format stream-json --verbose --model <model> --tools "" --no-session-persistence --setting-sources project --strict-mcp-config --system-prompt <基本指示>`。`--bare` は使わない（ログイン情報も読まなくなる）。
- agy のコマンド: `--input-format stream-json --output-format stream-json --model <model> --print=`（`-p` を先頭に置かない）。入力は `{"event":"user","message":{"role":"user","content":...}}`。
- codex のコマンド: `app-server`（JSON-RPC、1 行 1 メッセージ）。`initialize` → `initialized` 通知 → `thread/start`（`approvalPolicy:"never"`, `sandbox:"read-only"`）→ `turn/start`。
- CLI は `<PATH_LOCAL>/ai_cli_workspace`（空フォルダ）を作業フォルダにして起動する。Windows では `CREATE_NO_WINDOW`。
- claude のモデル一覧は固定 `["haiku", "sonnet", "opus"]`。codex は `codex debug models`、agy は `agy models` から取る（30 秒でタイムアウト、失敗時は空リスト）。
- テストは unittest 形式で `src-python/test/` に置き、`.venv\Scripts\python -m pytest -q` で実行する。関数名は camelCase。
- 全コミットの末尾に `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>` を付ける。
- 作業ブランチは `feat/ai-cli-translation`。

## Review Focus

1. **CLI の応答が返らない（固まる）とき**: 翻訳パイプラインが止まり続けず、60 秒で失敗して次の翻訳でセッションが作り直されること。→ Task 1 の `test_turn_timeout_raises_and_next_turn_restarts`
2. **CLI プロセスが途中で落ちたとき**: 例外で落ちずに `AiCliError` になり、次の翻訳で作り直されること。→ Task 1 の `test_process_exit_raises_and_next_turn_restarts`
3. **アプリ終了時に CLI の常駐プロセスが残らないこと**。→ Task 6 の `test_model_close_closes_the_ai_cli_client` と Task 7 の `test_shutdown_closes_ai_cli_sessions`
4. **CLI が入っていない PC で起動したとき**: AI CLI が「使えない」扱いになり、ネット接続だけを理由に使える扱いにならないこと（`controller.init()` の `case _` の既定動作）。→ Task 7 の `test_init_marks_ai_cli_unavailable_when_no_cli_installed`
5. **CLI を切り替えたとき**: 前の CLI のセッションが閉じられ、モデルは CLI ごとに覚えた値に戻ること。→ Task 5 の `test_set_tool_closes_previous_session` と Task 7 の `test_selected_model_is_remembered_per_tool`

---

### Task 1: 常駐セッションの共通基底と偽 CLI

**Files:**
- Create: `src-python/models/translation/ai_cli/__init__.py`
- Create: `src-python/models/translation/ai_cli/base.py`
- Create: `src-python/test/fixtures/fake_ai_cli.py`
- Create: `src-python/test/test_ai_cli_base.py`

**Interfaces:**
- Consumes: なし
- Produces:
  - `class AiCliError(Exception)`
  - `class CliSession(command_prefix: list[str], model: str, workspace: str, base_instructions: str)`。属性 `MAX_TURNS = 50`, `START_TIMEOUT = 120.0`, `TURN_TIMEOUT = 60.0`。メソッド `start() -> None`, `translate(prompt: str, timeout: float | None = None) -> str`, `close() -> None`, `isAlive() -> bool`。
  - サブクラスが実装するフック: `_buildArgs() -> list[str]`, `_afterStart(deadline: float) -> None`（既定は何もしない）, `_turnMessages(prompt: str) -> list[dict]`, `_interpret(message: dict) -> tuple[str, str] | None`（`("ok", text)` / `("error", detail)` / `None`=続きを待つ）。
  - サブクラス向けの補助: `_write(obj: dict) -> None`, `_next(deadline: float) -> dict`。
  - 偽 CLI: `python fake_ai_cli.py --mode claude|agy|codex [--hang-on TEXT] [--exit-on TEXT] [--error-on TEXT] [--permission-on TEXT] [--record FILE]`。翻訳要求には `T(<プロンプトの最終行>)` を返す。`--record` には起動時に `{"argv": [...]}`、受信ごとに `{"in": <受信 JSON>}` を 1 行ずつ追記する。

- [ ] **Step 1: 偽 CLI を書く**

`src-python/test/fixtures/fake_ai_cli.py`:
```python
"""AI CLI (claude / agy / codex) の常駐プロトコルをまねる偽 CLI。テスト専用。

翻訳要求には "T(<プロンプトの最終行>)" を返す。オプションで固まる・落ちる・
エラーを返す・(agy の) ツール許可を求める、を再現する。
"""

import argparse
import json
import sys
import time


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=["claude", "agy", "codex"])
    parser.add_argument("--hang-on")
    parser.add_argument("--exit-on")
    parser.add_argument("--error-on")
    parser.add_argument("--permission-on")
    parser.add_argument("--record")
    args, _unknown = parser.parse_known_args()

    def record(obj):
        if args.record:
            with open(args.record, "a", encoding="utf-8") as f:
                f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def emit(obj):
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    record({"argv": sys.argv[1:]})
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        message = json.loads(raw)
        record({"in": message})
        if args.mode == "codex":
            method = message.get("method")
            if method == "initialize":
                emit({"id": message["id"], "result": {"userAgent": "fake"}})
                continue
            if method == "thread/start":
                emit({"id": message["id"], "result": {"thread": {"id": "th-1"}}})
                continue
            if method != "turn/start":
                continue
            prompt = message["params"]["input"][0]["text"]
        elif args.mode == "claude":
            prompt = message["message"]["content"]
        else:
            prompt = message["message"]["content"]

        if args.hang_on and args.hang_on in prompt:
            time.sleep(3600)
        if args.exit_on and args.exit_on in prompt:
            sys.exit(3)
        answer = "T(" + prompt.strip().splitlines()[-1] + ")"
        failed = bool(args.error_on and args.error_on in prompt)

        if args.mode == "claude":
            emit({"type": "assistant", "message": {"content": [{"type": "text", "text": answer}]}})
            emit({"type": "result", "subtype": "error" if failed else "success",
                  "is_error": failed, "result": "boom" if failed else answer})
        elif args.mode == "agy":
            if args.permission_on and args.permission_on in prompt:
                emit({"event": "ask_permission", "ask_permission": {"tool": "browser_click_element"}})
                continue
            emit({"event": "step_update", "step_update": {"text_delta": answer}})
            if failed:
                emit({"event": "result", "result": {"status": "ERROR", "response": "", "error": "boom"}})
            else:
                emit({"event": "result", "result": {"status": "SUCCESS", "response": answer + "\n"}})
        else:
            emit({"id": message["id"], "result": {"turn": {"id": "turn-1"}}})
            if failed:
                emit({"method": "turn/completed", "params": {"turn": {"status": "failed", "error": {"message": "boom"}}}})
            else:
                emit({"method": "item/completed", "params": {"item": {"type": "agentMessage", "text": answer}}})
                emit({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 失敗するテストを書く**

`src-python/test/test_ai_cli_base.py`:
```python
"""CliSession（常駐セッションの共通基底）のテスト。偽 CLI を実際に起動する。"""

import json
import os
import sys
import tempfile
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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: 失敗を確認する**

Run: `.venv\Scripts\python -m pytest src-python/test/test_ai_cli_base.py -q`
Expected: FAIL（`models.translation.ai_cli` が無い）

- [ ] **Step 4: 実装する**

`src-python/models/translation/ai_cli/__init__.py`:
```python
"""AI CLI (codex / claude / agy) を常駐させて翻訳に使うためのパッケージ。"""
```

`src-python/models/translation/ai_cli/base.py`:
```python
"""AI CLI を常駐させる共通部分。

CLI を翻訳のたびに起動すると 1 回 10〜100 秒かかる (2026-09-24 実測) ため、
1 本のプロセスを起動したままにして 1 行 1 JSON でターンを送る。
CLI ごとの違い (起動引数・送る JSON・完了の見分け方) はサブクラスが持つ。
"""

import json
import os
import queue
import subprocess
import threading
import time
from collections import deque
from typing import Optional

_EOF = object()
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class AiCliError(Exception):
    """CLI セッションで翻訳できなかった (起動失敗・タイムアウト・異常終了・エラー応答)。"""


class CliSession:
    MAX_TURNS = 50
    START_TIMEOUT = 120.0
    TURN_TIMEOUT = 60.0

    def __init__(self, command_prefix: list[str], model: str, workspace: str, base_instructions: str) -> None:
        self.command_prefix = list(command_prefix)
        self.model = model
        self.workspace = workspace
        self.base_instructions = base_instructions
        self._lock = threading.RLock()
        self._proc: Optional[subprocess.Popen] = None
        self._messages: "queue.Queue" = queue.Queue()
        self._stderr: deque = deque(maxlen=50)
        self._turns = 0

    # --- サブクラスが実装する ---
    def _buildArgs(self) -> list[str]:
        raise NotImplementedError

    def _afterStart(self, deadline: float) -> None:
        """起動直後のハンドシェイク (codex の initialize など)。既定は何もしない。"""

    def _turnMessages(self, prompt: str) -> list[dict]:
        raise NotImplementedError

    def _interpret(self, message: dict):
        """("ok", text) / ("error", detail) / None (続きを待つ) を返す。"""
        raise NotImplementedError

    # --- 公開メソッド ---
    def isAlive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> None:
        with self._lock:
            if self.isAlive():
                return
            self._spawn()
            try:
                self._afterStart(time.monotonic() + self.START_TIMEOUT)
            except Exception:
                self.close()
                raise

    def translate(self, prompt: str, timeout: Optional[float] = None) -> str:
        with self._lock:
            if self.isAlive() and self._turns >= self.MAX_TURNS:
                self.close()
            if not self.isAlive():
                self.start()
            # 起動直後の最初のターンはモデルの準備 (claude で約 30 秒) を含むので長めに待つ。
            limit = self.START_TIMEOUT if self._turns == 0 else (timeout or self.TURN_TIMEOUT)
            deadline = time.monotonic() + limit
            try:
                for message in self._turnMessages(prompt):
                    self._write(message)
                while True:
                    verdict = self._interpret(self._next(deadline))
                    if verdict is None:
                        continue
                    self._turns += 1
                    kind, value = verdict
                    if kind == "ok":
                        return str(value).strip()
                    raise AiCliError(str(value))
            except AiCliError:
                self.close()
                raise
            except Exception as e:
                self.close()
                raise AiCliError(str(e)) from e

    def close(self) -> None:
        with self._lock:
            proc = self._proc
            self._proc = None
            if proc is None:
                return
            try:
                if proc.poll() is None:
                    if os.name == "nt":
                        # codex.cmd -> node のように子プロセスがいるので木ごと止める。
                        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                                       capture_output=True, creationflags=_CREATE_NO_WINDOW)
                    else:
                        proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass

    # --- サブクラス向けの補助 ---
    def _write(self, obj: dict) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise AiCliError("AI CLI process is not running")
        try:
            proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
            proc.stdin.flush()
        except OSError as e:
            raise AiCliError(f"failed to write to AI CLI: {e}") from e

    def _next(self, deadline: float) -> dict:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AiCliError("AI CLI did not respond in time")
        try:
            item = self._messages.get(timeout=remaining)
        except queue.Empty:
            raise AiCliError("AI CLI did not respond in time") from None
        if item is _EOF:
            tail = " | ".join(list(self._stderr)[-5:])
            raise AiCliError(f"AI CLI exited unexpectedly: {tail}")
        return item

    # --- 内部 ---
    def _spawn(self) -> None:
        os.makedirs(self.workspace, exist_ok=True)
        try:
            proc = subprocess.Popen(
                self.command_prefix + self._buildArgs(),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=self.workspace, text=True, encoding="utf-8", errors="replace",
                creationflags=_CREATE_NO_WINDOW,
            )
        except OSError as e:
            raise AiCliError(f"failed to start AI CLI: {e}") from e
        self._proc = proc
        self._messages = queue.Queue()
        self._turns = 0
        threading.Thread(target=self._readStdout, args=(proc, self._messages), daemon=True).start()
        threading.Thread(target=self._readStderr, args=(proc,), daemon=True).start()

    @staticmethod
    def _readStdout(proc: subprocess.Popen, messages: "queue.Queue") -> None:
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    messages.put(obj)
        except Exception:
            pass
        finally:
            messages.put(_EOF)

    def _readStderr(self, proc: subprocess.Popen) -> None:
        try:
            for line in proc.stderr:
                self._stderr.append(line.rstrip())
        except Exception:
            pass
```

- [ ] **Step 5: テストが通ることを確認する**

Run: `.venv\Scripts\python -m pytest src-python/test/test_ai_cli_base.py -q`
Expected: 8 passed

- [ ] **Step 6: Commit**

```powershell
git add src-python/models/translation/ai_cli/ src-python/test/fixtures/fake_ai_cli.py src-python/test/test_ai_cli_base.py
git commit -m "feat(translation): AI CLI を常駐させる共通セッションを追加"
```

---

### Task 2: claude と agy のアダプター

**Files:**
- Create: `src-python/models/translation/ai_cli/claude_session.py`
- Create: `src-python/models/translation/ai_cli/agy_session.py`
- Create: `src-python/test/test_ai_cli_stream_sessions.py`

**Interfaces:**
- Consumes: Task 1 の `CliSession`, `AiCliError`、偽 CLI
- Produces: `ClaudeSession(CliSession)`, `AgySession(CliSession)`（コンストラクタは基底と同じ）

- [ ] **Step 1: 失敗するテストを書く**

`src-python/test/test_ai_cli_stream_sessions.py`:
```python
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
```

- [ ] **Step 2: 失敗を確認する**

Run: `.venv\Scripts\python -m pytest src-python/test/test_ai_cli_stream_sessions.py -q`
Expected: FAIL（モジュールが無い）

- [ ] **Step 3: 実装する**

`src-python/models/translation/ai_cli/claude_session.py`:
```python
"""Claude Code (`claude`) を stream-json で常駐させるアダプター。

ユーザー設定 (フック・プラグイン・MCP) を読むと 1 ターン 17 秒以上かかる
(2026-09-24 実測) ので `--setting-sources project --strict-mcp-config` で
読ませない。`--bare` はログイン情報も読まなくなるので使わない。
"""

try:
    from .base import CliSession
except ImportError:
    from base import CliSession


class ClaudeSession(CliSession):
    def _buildArgs(self) -> list[str]:
        return [
            "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--model", self.model, "--tools", "", "--no-session-persistence",
            "--setting-sources", "project", "--strict-mcp-config",
            "--system-prompt", self.base_instructions,
        ]

    def _turnMessages(self, prompt: str) -> list[dict]:
        return [{"type": "user", "message": {"role": "user", "content": prompt}}]

    def _interpret(self, message: dict):
        if message.get("type") != "result":
            return None
        if message.get("is_error"):
            return ("error", str(message.get("result") or message.get("subtype") or "claude error"))
        return ("ok", str(message.get("result", "")))
```

`src-python/models/translation/ai_cli/agy_session.py`:
```python
"""Antigravity CLI (`agy`) を stream-json で常駐させるアダプター。

`-p` を先頭に置くと次のフラグをプロンプトとして読んでしまうので、空の値を
付けた `--print=` を最後に置く。入力は `{"event":"user","message":{...}}`。
agy にはシステムプロンプトの指定が無いので、指示は毎ターンのプロンプトに含める
(AICliClient が組み立てる)。ツールの使用許可を求められたら許可しない。
"""

try:
    from .base import CliSession
except ImportError:
    from base import CliSession


class AgySession(CliSession):
    def _buildArgs(self) -> list[str]:
        return ["--input-format", "stream-json", "--output-format", "stream-json",
                "--model", self.model, "--print="]

    def _turnMessages(self, prompt: str) -> list[dict]:
        return [{"event": "user", "message": {"role": "user", "content": prompt}}]

    def _interpret(self, message: dict):
        event = str(message.get("event", ""))
        if "permission" in event:
            return ("error", f"agy asked for a tool permission ({event}); refused")
        if event != "result":
            return None
        result = message.get("result") or {}
        if result.get("status") == "SUCCESS":
            return ("ok", str(result.get("response", "")))
        return ("error", str(result.get("error") or result.get("status") or "agy error"))
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `.venv\Scripts\python -m pytest src-python/test/test_ai_cli_stream_sessions.py -q`
Expected: 5 passed

- [ ] **Step 5: Commit**

```powershell
git add src-python/models/translation/ai_cli/claude_session.py src-python/models/translation/ai_cli/agy_session.py src-python/test/test_ai_cli_stream_sessions.py
git commit -m "feat(translation): claude と agy の常駐アダプターを追加"
```

---

### Task 3: codex のアダプター

**Files:**
- Create: `src-python/models/translation/ai_cli/codex_session.py`
- Create: `src-python/test/test_ai_cli_codex_session.py`

**Interfaces:**
- Consumes: Task 1 の `CliSession`, `AiCliError`, `_write`, `_next`、偽 CLI
- Produces: `CodexSession(CliSession)`

- [ ] **Step 1: 失敗するテストを書く**

`src-python/test/test_ai_cli_codex_session.py`:
```python
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
```

- [ ] **Step 2: 失敗を確認する**

Run: `.venv\Scripts\python -m pytest src-python/test/test_ai_cli_codex_session.py -q`
Expected: FAIL（モジュールが無い）

- [ ] **Step 3: 実装する**

`src-python/models/translation/ai_cli/codex_session.py`:
```python
"""OpenAI Codex CLI (`codex app-server`) を常駐させるアダプター。

app-server は 1 行 1 メッセージの JSON-RPC。initialize → initialized 通知 →
thread/start で 1 本のスレッドを作り、翻訳ごとに turn/start を送る。
応答は item/completed (item.type == "agentMessage") の text で、
turn/completed が来たら完了。読み取り専用・承認なしで動かす。
"""

try:
    from .base import AiCliError, CliSession
except ImportError:
    from base import AiCliError, CliSession


class CodexSession(CliSession):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._rpc_id = 0
        self._thread_id = None
        self._turn_request_id = None
        self._texts: list[str] = []

    def _buildArgs(self) -> list[str]:
        return ["app-server"]

    def _newId(self) -> int:
        self._rpc_id += 1
        return self._rpc_id

    def _request(self, method: str, params: dict, deadline: float) -> dict:
        request_id = self._newId()
        self._write({"method": method, "id": request_id, "params": params})
        while True:
            message = self._next(deadline)
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise AiCliError(f"codex {method} failed: {message['error']}")
            return message.get("result") or {}

    def _afterStart(self, deadline: float) -> None:
        self._rpc_id = 0
        self._request("initialize", {"clientInfo": {"name": "vrct", "version": "1"}}, deadline)
        self._write({"method": "initialized"})
        result = self._request("thread/start", {
            "model": self.model,
            "approvalPolicy": "never",
            "sandbox": "read-only",
            "baseInstructions": self.base_instructions,
        }, deadline)
        thread_id = (result.get("thread") or {}).get("id")
        if not thread_id:
            raise AiCliError("codex thread/start returned no thread id")
        self._thread_id = thread_id

    def _turnMessages(self, prompt: str) -> list[dict]:
        self._texts = []
        self._turn_request_id = self._newId()
        return [{"method": "turn/start", "id": self._turn_request_id,
                 "params": {"threadId": self._thread_id, "input": [{"type": "text", "text": prompt}]}}]

    def _interpret(self, message: dict):
        if message.get("id") == self._turn_request_id and "error" in message:
            return ("error", f"codex turn/start failed: {message['error']}")
        method = message.get("method")
        params = message.get("params") or {}
        if method == "item/completed":
            item = params.get("item") or {}
            if item.get("type") == "agentMessage" and item.get("text"):
                self._texts.append(str(item["text"]))
            return None
        if method == "turn/completed":
            turn = params.get("turn") or {}
            if turn.get("status") in ("failed", "interrupted"):
                return ("error", f"codex turn {turn.get('status')}: {turn.get('error')}")
            return ("ok", "\n".join(self._texts))
        if method == "error":
            return ("error", f"codex error: {params}")
        return None
```

- [ ] **Step 4: 推論の強さの指定ができるか確かめる**

実際の codex で 1 回だけ確かめる（利用枠を少し使う）。`turn/start` の `params` に `"effort": "low"` を足したときにエラー応答にならず、翻訳が返るかを見る:
```powershell
$env:VRCT_CODEX_EFFORT_PROBE = "1"
.venv\Scripts\python -X utf8 -c "import sys; sys.path.insert(0,'src-python'); import shutil; from models.translation.ai_cli.codex_session import CodexSession as C; exe=shutil.which('codex.cmd') or shutil.which('codex'); s=C(command_prefix=[exe], model='gpt-5.5', workspace=r'.\ai_cli_probe', base_instructions='Output only the translation.'); orig=s._turnMessages; s._turnMessages=lambda p: [dict(m, params=dict(m['params'], effort='low')) for m in orig(p)]; print(s.translate('Translate into English: こんにちは')); s.close()"
Remove-Item -Recurse -Force .\ai_cli_probe
```
- 翻訳が返れば、`_turnMessages` の `params` に `"effort": "low"` を加える（テストの `sent[3]["params"]` に `effort` があることも確かめる 1 行を `test_handshake_then_turn` に足す）。
- エラーになれば何も足さず、結果（エラー文）をコミットメッセージの本文に書く。

- [ ] **Step 5: テストが通ることを確認する**

Run: `.venv\Scripts\python -m pytest src-python/test/test_ai_cli_codex_session.py -q`
Expected: 3 passed

- [ ] **Step 6: Commit**

```powershell
git add src-python/models/translation/ai_cli/codex_session.py src-python/test/test_ai_cli_codex_session.py
git commit -m "feat(translation): codex app-server の常駐アダプターを追加"
```

---

### Task 4: 導入済み CLI とモデル一覧の検出

**Files:**
- Create: `src-python/models/translation/ai_cli/catalog.py`
- Create: `src-python/test/test_ai_cli_catalog.py`

**Interfaces:**
- Consumes: なし
- Produces:
  - `TOOLS = ("codex", "claude", "agy")`
  - `CLAUDE_MODELS = ["haiku", "sonnet", "opus"]`
  - `resolveExecutable(tool: str) -> str | None`
  - `detectInstalledTools() -> list[str]`
  - `listModels(tool: str) -> list[str]`

- [ ] **Step 1: 失敗するテストを書く**

`src-python/test/test_ai_cli_catalog.py`:
```python
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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 失敗を確認する**

Run: `.venv\Scripts\python -m pytest src-python/test/test_ai_cli_catalog.py -q`
Expected: FAIL（モジュールが無い）

- [ ] **Step 3: 実装する**

`src-python/models/translation/ai_cli/catalog.py`:
```python
"""導入済みの AI CLI とモデル一覧を調べる。"""

import json
import shutil
import subprocess

try:
    from utils import errorLogging
except ImportError:
    import sys
    from os import path as os_path
    sys.path.append(os_path.dirname(os_path.dirname(os_path.dirname(os_path.dirname(os_path.abspath(__file__))))))
    from utils import errorLogging

TOOLS = ("codex", "claude", "agy")
# claude にはモデル一覧を返すコマンドが無いので別名を固定で並べる。
CLAUDE_MODELS = ["haiku", "sonnet", "opus"]
_LIST_TIMEOUT_SEC = 30
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# codex は npm のシム (codex.cmd / codex.ps1) で入るので .cmd を優先する。
_EXECUTABLE_NAMES = {"codex": ("codex.cmd", "codex"), "claude": ("claude",), "agy": ("agy",)}


def resolveExecutable(tool: str):
    for name in _EXECUTABLE_NAMES.get(tool, ()):
        path = shutil.which(name)
        if path:
            return path
    return None


def detectInstalledTools() -> list[str]:
    return [tool for tool in TOOLS if resolveExecutable(tool)]


def _run(executable: str, args: list[str]) -> str:
    result = subprocess.run([executable, *args], capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=_LIST_TIMEOUT_SEC, creationflags=_CREATE_NO_WINDOW)
    return result.stdout or ""


def listModels(tool: str) -> list[str]:
    if tool == "claude":
        return list(CLAUDE_MODELS)
    executable = resolveExecutable(tool)
    if executable is None:
        return []
    try:
        if tool == "codex":
            data = json.loads(_run(executable, ["debug", "models"]))
            entries = data.get("models", []) if isinstance(data, dict) else data
            slugs = [str(entry.get("slug")) for entry in entries if isinstance(entry, dict) and entry.get("slug")]
            return [slug for slug in slugs if "review" not in slug]
        if tool == "agy":
            models = []
            for line in _run(executable, ["models"]).splitlines():
                if "\t" in line:
                    model_id = line.split("\t", 1)[0].strip()
                    if model_id:
                        models.append(model_id)
            return models
    except Exception:
        errorLogging()
    return []
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `.venv\Scripts\python -m pytest src-python/test/test_ai_cli_catalog.py -q`
Expected: 8 passed

- [ ] **Step 5: Commit**

```powershell
git add src-python/models/translation/ai_cli/catalog.py src-python/test/test_ai_cli_catalog.py
git commit -m "feat(translation): 導入済み AI CLI とモデル一覧の検出を追加"
```

---

### Task 5: AICliClient と言語・プロンプト設定

**Files:**
- Create: `src-python/models/translation/translation_ai_cli.py`
- Create: `src-python/models/translation/translation_settings/prompt/translation_ai_cli.yml`
- Modify: `src-python/models/translation/translation_settings/languages/languages.yml`（`OpenRouter_API` の後に追加）
- Create: `src-python/test/test_translation_ai_cli_client.py`

**Interfaces:**
- Consumes: Task 1〜4 の `AiCliError`, `ClaudeSession`, `AgySession`, `CodexSession`, `catalog.resolveExecutable/detectInstalledTools/listModels`。既存の `translation_llm_common.buildSystemPrompt`, `translation_utils.loadTranslatePromptConfig`, `translation_languages.translation_lang`
- Produces: `class AICliClient(root_path: str | None = None, workspace: str | None = None)`。メソッド `getInstalledTools() -> list[str]`, `getTool() -> str | None`, `setTool(tool) -> bool`, `getModelList() -> list[str]`, `getModel() -> str | None`, `setModel(model) -> bool`, `authenticationCheck() -> bool`, `updateClient() -> None`, `setContextHistory(items) -> None`, `translate(text, input_lang, output_lang) -> str`, `close() -> None`。定数 `BASE_INSTRUCTIONS`。

- [ ] **Step 1: 言語とプロンプトの設定を足す**

`languages.yml` の `OpenRouter_API:` ブロックの後に:
```yaml

AI_CLI:
  source: *openai_langs
  target: *openai_langs
```

`translation_settings/prompt/translation_ai_cli.yml`:
```yaml
system_prompt: |
  You are a translation engine inside a chat app. You are not a coding assistant.
  Supported languages:
  {supported_languages}

  Translate the text after "Text to translate:" from {input_lang} to {output_lang}.
  Return ONLY the translated text. Do not add quotes, notes, or extra commentary.
  Never run tools or commands.
history:
  use_history: true
  sources: [chat, mic, speaker]
  max_messages: 5
  max_chars: 4000
  header_template: |
    Conversation context (recent {max_messages} messages):
    {history}
  item_template: "[{timestamp}][{source}] {text}"
```

- [ ] **Step 2: 失敗するテストを書く**

`src-python/test/test_translation_ai_cli_client.py`:
```python
import unittest
from unittest.mock import MagicMock, patch

from models.translation import translation_ai_cli as mod
from models.translation.translation_ai_cli import AICliClient


class AICliClientTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(mod.catalog, "detectInstalledTools", return_value=["codex", "claude"])
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(mod.catalog, "resolveExecutable", side_effect=lambda t: f"C:/bin/{t}.exe" if t in ("codex", "claude") else None)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(mod.catalog, "listModels", side_effect=lambda t: {"claude": ["haiku", "sonnet"], "codex": ["gpt-5.5"]}.get(t, []))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = AICliClient(workspace="C:/tmp/ws")
        self.addCleanup(self.client.close)

    def test_set_tool_accepts_only_installed(self):
        self.assertTrue(self.client.setTool("claude"))
        self.assertFalse(self.client.setTool("agy"))
        self.assertEqual(self.client.getTool(), "claude")

    def test_model_must_be_in_the_tool_list(self):
        self.client.setTool("claude")
        self.assertEqual(self.client.getModelList(), ["haiku", "sonnet"])
        self.assertTrue(self.client.setModel("sonnet"))
        self.assertFalse(self.client.setModel("gpt-5.5"))
        self.assertEqual(self.client.getModel(), "sonnet")

    def test_authentication_check_needs_an_installed_tool(self):
        self.assertFalse(self.client.authenticationCheck())
        self.client.setTool("codex")
        self.assertTrue(self.client.authenticationCheck())

    def test_translate_builds_prompt_and_uses_tool_session(self):
        self.client.setTool("claude")
        self.client.setModel("haiku")
        fake_session = MagicMock()
        fake_session.translate.return_value = "Hello"
        with patch.dict(mod.SESSION_CLASSES, {"claude": MagicMock(return_value=fake_session)}) as classes:
            result = self.client.translate("こんにちは", "Japanese", "English")
        self.assertEqual(result, "Hello")
        kwargs = classes["claude"].call_args.kwargs
        self.assertEqual(kwargs["command_prefix"], ["C:/bin/claude.exe"])
        self.assertEqual(kwargs["model"], "haiku")
        self.assertEqual(kwargs["workspace"], "C:/tmp/ws")
        prompt = fake_session.translate.call_args.args[0]
        self.assertIn("from Japanese to English", prompt)
        self.assertTrue(prompt.endswith("Text to translate:\nこんにちは"))

    def test_set_tool_closes_previous_session(self):
        self.client.setTool("claude")
        self.client.setModel("haiku")
        first = MagicMock()
        first.translate.return_value = "x"
        with patch.dict(mod.SESSION_CLASSES, {"claude": MagicMock(return_value=first)}):
            self.client.translate("a", "Japanese", "English")
        self.client.setTool("codex")
        first.close.assert_called_once()

    def test_set_model_to_a_different_value_closes_session(self):
        self.client.setTool("claude")
        self.client.setModel("haiku")
        first = MagicMock()
        first.translate.return_value = "x"
        with patch.dict(mod.SESSION_CLASSES, {"claude": MagicMock(return_value=first)}):
            self.client.translate("a", "Japanese", "English")
        self.client.setModel("sonnet")
        first.close.assert_called_once()

    def test_translate_without_model_raises(self):
        self.client.setTool("claude")
        with self.assertRaises(mod.AiCliError):
            self.client.translate("a", "Japanese", "English")

    def test_update_client_warms_up_in_background(self):
        self.client.setTool("claude")
        self.client.setModel("haiku")
        session = MagicMock()
        with patch.dict(mod.SESSION_CLASSES, {"claude": MagicMock(return_value=session)}), \
             patch.object(mod.threading, "Thread") as mock_thread:
            self.client.updateClient()
            target = mock_thread.call_args.kwargs["target"]
            target()
        session.start.assert_called_once()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: 失敗を確認する**

Run: `.venv\Scripts\python -m pytest src-python/test/test_translation_ai_cli_client.py -q`
Expected: FAIL（モジュールが無い）

- [ ] **Step 4: 実装する**

`src-python/models/translation/translation_ai_cli.py`:
```python
"""翻訳エンジン「AI CLI」: ユーザーの PC にある codex / claude / agy を常駐させて翻訳する。

CLI ごとに常駐セッションを 1 本持つ。CLI かモデルを変えたらセッションを閉じ、
次の翻訳か updateClient() (裏で起動しておく) で作り直す。入力・出力言語は
翻訳ごとに変わるので、指示は毎回のプロンプトに含める。
"""

import os
import threading
from typing import Optional

try:
    from .ai_cli import catalog
    from .ai_cli.agy_session import AgySession
    from .ai_cli.base import AiCliError
    from .ai_cli.claude_session import ClaudeSession
    from .ai_cli.codex_session import CodexSession
    from .translation_languages import translation_lang
    from .translation_llm_common import buildSystemPrompt
    from .translation_utils import loadTranslatePromptConfig
except ImportError:
    import sys
    from os import path as os_path
    sys.path.append(os_path.dirname(os_path.dirname(os_path.dirname(os_path.abspath(__file__)))))
    from models.translation.ai_cli import catalog
    from models.translation.ai_cli.agy_session import AgySession
    from models.translation.ai_cli.base import AiCliError
    from models.translation.ai_cli.claude_session import ClaudeSession
    from models.translation.ai_cli.codex_session import CodexSession
    from models.translation.translation_languages import translation_lang
    from models.translation.translation_llm_common import buildSystemPrompt
    from models.translation.translation_utils import loadTranslatePromptConfig

SESSION_CLASSES = {"codex": CodexSession, "claude": ClaudeSession, "agy": AgySession}
BASE_INSTRUCTIONS = "You are a translation engine. Output only the translation of the given text."


class AICliClient:
    def __init__(self, root_path: Optional[str] = None, workspace: Optional[str] = None) -> None:
        prompt_config = loadTranslatePromptConfig(root_path, "translation_ai_cli.yml")
        self.prompt_template = prompt_config["system_prompt"]
        self.history_cfg = prompt_config.get("history", {"use_history": False})
        self.supported_languages = list(translation_lang["AI_CLI"]["source"].keys())
        self.workspace = workspace or os.path.join(root_path or ".", "ai_cli_workspace")
        self.tool: Optional[str] = None
        self.model: Optional[str] = None
        self._context_history: list[dict] = []
        self._session = None
        self._lock = threading.Lock()

    def getInstalledTools(self) -> list[str]:
        return catalog.detectInstalledTools()

    def getTool(self) -> Optional[str]:
        return self.tool

    def setTool(self, tool: str) -> bool:
        if tool not in self.getInstalledTools():
            return False
        if tool != self.tool:
            self.close()
            self.tool = tool
            self.model = None
        return True

    def getModelList(self) -> list[str]:
        return catalog.listModels(self.tool) if self.tool else []

    def getModel(self) -> Optional[str]:
        return self.model

    def setModel(self, model: str) -> bool:
        if model not in self.getModelList():
            return False
        if model != self.model:
            self.close()
            self.model = model
        return True

    def authenticationCheck(self) -> bool:
        return self.tool is not None and catalog.resolveExecutable(self.tool) is not None

    def setContextHistory(self, history_items: list[dict]) -> None:
        self._context_history = history_items or []

    def _ensureSession(self):
        with self._lock:
            if self.tool is None or not self.model:
                raise AiCliError("AI CLI tool or model is not selected")
            if self._session is None:
                executable = catalog.resolveExecutable(self.tool)
                if executable is None:
                    raise AiCliError(f"{self.tool} is not installed")
                self._session = SESSION_CLASSES[self.tool](
                    command_prefix=[executable], model=self.model,
                    workspace=self.workspace, base_instructions=BASE_INSTRUCTIONS,
                )
            return self._session

    def updateClient(self) -> None:
        """セッションを裏で起動しておく (claude の起動 30 秒を最初の翻訳で待たないため)。"""
        def warmUp():
            try:
                self._ensureSession().start()
            except Exception:
                pass  # 起動に失敗しても、次の翻訳でもう一度試してエラーを返す
        threading.Thread(target=warmUp, name="ai-cli-warmup", daemon=True).start()

    def translate(self, text: str, input_lang: str, output_lang: str) -> str:
        instructions = buildSystemPrompt(
            self.prompt_template, self.supported_languages, input_lang, output_lang,
            self.history_cfg, self._context_history,
        )
        prompt = f"{instructions.rstrip()}\n\nText to translate:\n{text}"
        return self._ensureSession().translate(prompt)

    def close(self) -> None:
        with self._lock:
            session = self._session
            self._session = None
        if session is not None:
            session.close()
```

- [ ] **Step 5: テストが通ることを確認する**

Run: `.venv\Scripts\python -m pytest src-python/test/test_translation_ai_cli_client.py -q`
Expected: 8 passed

- [ ] **Step 6: 全テストを流す**

Run: `.venv\Scripts\python -m pytest -q`
Expected: 失敗なし（`languages.yml` に `AI_CLI` を足したことで、エンジン一覧を固定値で比べる既存テストが落ちる場合は、その一覧に `AI_CLI` を足す。例: `test_controller_translation_engine_language_fallback.py` の `ENGINES`）

- [ ] **Step 7: Commit**

```powershell
git add src-python/models/translation/translation_ai_cli.py src-python/models/translation/translation_settings/ src-python/test/
git commit -m "feat(translation): 翻訳エンジン AI CLI のクライアントを追加"
```

---

### Task 6: Translator・model・レジストリ・エラーコード

**Files:**
- Modify: `src-python/models/translation/translation_translator.py`（import 群 ~120、`__init__` ~147-150、Ollama メソッドの後 ~464、`translate()` の Ollama 分岐の後 ~660）
- Modify: `src-python/model.py`（Ollama の委譲の後 ~1213）
- Modify: `src-python/models/translation/translation_providers.py`（import、`CONNECTION_PROVIDER_REGISTRY`）
- Modify: `src-python/errors.py`（~143, ~152-154, ~609-634 のメタデータ）
- Create: `src-python/test/test_translation_translator_ai_cli.py`

**Interfaces:**
- Consumes: Task 5 の `AICliClient`、`catalog.detectInstalledTools`
- Produces:
  - Translator: `ai_cli_client`, `ai_cli_connected`, `getAiCliInstalledTools() -> list[str]`, `checkAiCliClient(tool: str, root_path: str | None = None) -> bool`, `getAiCliConnected() -> bool`, `getAiCliModelList() -> list[str]`, `setAiCliModel(model: str) -> bool`, `updateAiCliClient() -> None`, `closeAiCliClient() -> None`、`translate()` の `"AI_CLI"` 分岐
  - model: `getTranslatorAiCliInstalledTools()`, `getTranslatorAiCliConnected()`, `authenticationTranslatorAiCli()`, `getTranslatorAiCliModelList()`, `setTranslatorAiCliModel(model)`, `updateTranslatorAiCliClient()`, `setTranslatorAiCliTool(tool)`, `closeTranslatorAiCli()`
  - `ErrorCode.CONNECTION_AI_CLI_FAILED`, `ErrorCode.MODEL_AI_CLI_INVALID`
  - `CONNECTION_PROVIDER_REGISTRY["AI_CLI"]`（`selectable_model_list_attr="SELECTABLE_AI_CLI_MODEL_LIST"`, `selected_model_attr="SELECTED_AI_CLI_MODEL"`, `run_mapping_selectable_key="selectable_ai_cli_model_list"`, `run_mapping_selected_key="selected_ai_cli_model"`）

- [ ] **Step 1: 失敗するテストを書く**

`src-python/test/test_translation_translator_ai_cli.py`:
```python
import unittest
from unittest.mock import MagicMock, patch

from errors import ErrorCode
from models.translation import translation_translator as tt_module
from models.translation.translation_providers import CONNECTION_PROVIDER_REGISTRY
from models.translation.translation_translator import Translator


class TranslatorAiCliTests(unittest.TestCase):
    def test_check_client_connects_when_tool_is_installed(self):
        translator = Translator()
        fake_client = MagicMock()
        fake_client.setTool.return_value = True
        fake_client.authenticationCheck.return_value = True
        with patch.object(tt_module, "AICliClient", return_value=fake_client):
            self.assertTrue(translator.checkAiCliClient("claude", root_path="."))
        self.assertTrue(translator.getAiCliConnected())
        fake_client.setTool.assert_called_once_with("claude")

    def test_check_client_fails_and_closes_when_tool_missing(self):
        translator = Translator()
        fake_client = MagicMock()
        fake_client.setTool.return_value = False
        with patch.object(tt_module, "AICliClient", return_value=fake_client):
            self.assertFalse(translator.checkAiCliClient("agy", root_path="."))
        self.assertFalse(translator.getAiCliConnected())
        fake_client.close.assert_called_once()

    def test_translate_dispatches_to_ai_cli_client(self):
        translator = Translator()
        translator.ai_cli_client = MagicMock()
        translator.ai_cli_client.translate.return_value = "Hello"
        history = [{"source": "chat", "text": "x"}]
        result = translator.translate("AI_CLI", "", "Japanese", "English", "United States", "こんにちは", context_history=history)
        self.assertEqual(result, "Hello")
        translator.ai_cli_client.setContextHistory.assert_called_once_with(history)

    def test_translate_without_client_returns_false(self):
        translator = Translator()
        self.assertFalse(translator.translate("AI_CLI", "", "Japanese", "English", "United States", "こんにちは"))

    def test_translate_error_returns_false(self):
        translator = Translator()
        translator.ai_cli_client = MagicMock()
        translator.ai_cli_client.translate.side_effect = tt_module.AiCliError("timeout")
        self.assertFalse(translator.translate("AI_CLI", "", "Japanese", "English", "United States", "こんにちは"))

    def test_registry_entry(self):
        spec = CONNECTION_PROVIDER_REGISTRY["AI_CLI"]
        self.assertEqual(spec.error_connection_failed, ErrorCode.CONNECTION_AI_CLI_FAILED)
        self.assertEqual(spec.error_model_invalid, ErrorCode.MODEL_AI_CLI_INVALID)
        self.assertEqual(spec.selected_model_attr, "SELECTED_AI_CLI_MODEL")

    def test_model_close_closes_the_ai_cli_client(self):
        # アプリ終了時 (Controller.shutdown -> model.closeTranslatorAiCli) に
        # CLI の常駐プロセスを残さない。
        from model import Model
        model = object.__new__(Model)
        model._inited = True
        model._init_failed = False
        model.translator = Translator()
        model.translator.ai_cli_client = MagicMock()
        model.closeTranslatorAiCli()
        model.translator.ai_cli_client.close.assert_called_once()

    def test_model_close_before_init_does_nothing(self):
        from model import Model
        model = object.__new__(Model)
        model._inited = False
        model._init_failed = True
        model.closeTranslatorAiCli()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 失敗を確認する**

Run: `.venv\Scripts\python -m pytest src-python/test/test_translation_translator_ai_cli.py -q`
Expected: FAIL

- [ ] **Step 3: errors.py を直す**

`MODEL_OLLAMA_INVALID = "MODEL_OLLAMA_INVALID"` の次の行に `    MODEL_AI_CLI_INVALID = "MODEL_AI_CLI_INVALID"`、`CONNECTION_OLLAMA_FAILED = ...` の次の行に `    CONNECTION_AI_CLI_FAILED = "CONNECTION_AI_CLI_FAILED"`。
メタデータは `ErrorCode.MODEL_OLLAMA_INVALID` の辞書の後と `ErrorCode.CONNECTION_OLLAMA_FAILED` の辞書の後に:
```python
    ErrorCode.MODEL_AI_CLI_INVALID: {
        "category": ErrorCategory.MODEL,
        "message": "AI CLI model is not valid",
        "severity": "warning",
        "user_action_required": True,
    },
```
```python
    ErrorCode.CONNECTION_AI_CLI_FAILED: {
        "category": ErrorCategory.CONNECTION,
        "message": "The selected AI CLI is not installed or could not be started",
        "severity": "error",
        "user_action_required": True,
    },
```

- [ ] **Step 4: レジストリに足す**

`translation_providers.py` の `CONNECTION_PROVIDER_REGISTRY` の `"Ollama"` エントリの後に:
```python
    "AI_CLI": ConnectionEngineSpec(
        engine_key="AI_CLI",
        error_connection_failed=ErrorCode.CONNECTION_AI_CLI_FAILED,
        error_model_invalid=ErrorCode.MODEL_AI_CLI_INVALID,
        selectable_model_list_attr="SELECTABLE_AI_CLI_MODEL_LIST",
        selected_model_attr="SELECTED_AI_CLI_MODEL",
        run_mapping_selectable_key="selectable_ai_cli_model_list",
        run_mapping_selected_key="selected_ai_cli_model",
    ),
```
モジュール docstring の「疎通確認型、2エンジン: LMStudio/Ollama」を「3エンジン: LMStudio/Ollama/AI_CLI（AI_CLI はローカルの CLI を検出する）」に直す。

- [ ] **Step 5: Translator を直す**

import 群（Ollama の import の後）に:
```python
try:
    from .translation_ai_cli import AICliClient
    from .ai_cli import catalog as ai_cli_catalog
    from .ai_cli.base import AiCliError
except Exception:
    from translation_ai_cli import AICliClient
    from ai_cli import catalog as ai_cli_catalog
    from ai_cli.base import AiCliError
```
`__init__` の `self.ollama_connected: bool = False` の後に:
```python
        self.ai_cli_client: Optional[Any] = None
        self.ai_cli_connected: bool = False
```
`updateOllamaClient` の後に:
```python
    def getAiCliInstalledTools(self) -> list[str]:
        return ai_cli_catalog.detectInstalledTools()

    def getAiCliConnected(self) -> bool:
        return self.ai_cli_connected

    def checkAiCliClient(self, tool: str, root_path: str = None) -> bool:
        """選んだ AI CLI が入っていれば接続済みにする (セッションはまだ起動しない)。"""
        if self.ai_cli_client is None:
            self.ai_cli_client = AICliClient(root_path=root_path)
        result = bool(self.ai_cli_client.setTool(tool) and self.ai_cli_client.authenticationCheck())
        if result is False:
            self.ai_cli_client.close()
        self.ai_cli_connected = result
        return result

    def getAiCliModelList(self) -> list[str]:
        if self.ai_cli_client is None:
            return []
        return self.ai_cli_client.getModelList()

    def setAiCliModel(self, model: str) -> bool:
        if self.ai_cli_client is None:
            return False
        return self.ai_cli_client.setModel(model)

    def updateAiCliClient(self) -> None:
        if self.ai_cli_client is not None:
            self.ai_cli_client.updateClient()

    def closeAiCliClient(self) -> None:
        if self.ai_cli_client is not None:
            self.ai_cli_client.close()
```
`translate()` の `case "Ollama":` 分岐の後に:
```python
                case "AI_CLI":
                    if self.ai_cli_client is None:
                        result = False
                    else:
                        if context_history:
                            self.ai_cli_client.setContextHistory(context_history)
                        result = self.ai_cli_client.translate(
                            message,
                            input_lang=source_language,
                            output_lang=target_language,
                        )
```
（`AiCliError` は `translate()` 末尾の `except Exception: errorLogging(); result = False` に落ちる。）

- [ ] **Step 6: model を直す**

`updateTranslatorOllamaClient` の後に:
```python
    def getTranslatorAiCliInstalledTools(self) -> list[str]:
        self.ensure_initialized()
        return self.translator.getAiCliInstalledTools()

    def getTranslatorAiCliConnected(self) -> bool:
        self.ensure_initialized()
        return self.translator.getAiCliConnected()

    def authenticationTranslatorAiCli(self) -> bool:
        self.ensure_initialized()
        return self.translator.checkAiCliClient(tool=config.SELECTED_AI_CLI_TOOL, root_path=config.PATH_LOCAL)

    def setTranslatorAiCliTool(self, tool: str) -> bool:
        self.ensure_initialized()
        return self.translator.checkAiCliClient(tool=tool, root_path=config.PATH_LOCAL)

    def getTranslatorAiCliModelList(self) -> list[str]:
        self.ensure_initialized()
        return self.translator.getAiCliModelList()

    def setTranslatorAiCliModel(self, model: str) -> bool:
        self.ensure_initialized()
        return self.translator.setAiCliModel(model=model)

    def updateTranslatorAiCliClient(self) -> None:
        self.ensure_initialized()
        self.translator.updateAiCliClient()

    def closeTranslatorAiCli(self) -> None:
        if getattr(self, "_inited", False):
            self.translator.closeAiCliClient()
```
`config.SELECTED_AI_CLI_TOOL` は Task 7 で足す。このタスクのテストは model を通らないので、ここでは未定義のままでよい。

- [ ] **Step 7: テストが通ることを確認する**

Run: `.venv\Scripts\python -m pytest src-python/test/test_translation_translator_ai_cli.py -q`
Expected: 8 passed。`test_translation_providers.py` は Task 7 まで config 属性が無いので落ちる。落ちるのがこのファイルの `AI_CLI` 関連だけであることを確認し、Task 7 で直す旨をコミット本文に書く。

- [ ] **Step 8: Commit**

```powershell
git add src-python/
git commit -m "feat(translation): AI CLI を Translator・model・レジストリに配線"
```

---

### Task 7: 設定・controller・mainloop・起動時の確認・終了処理

**Files:**
- Modify: `src-python/config.py`（Selectable 宣言 ~813-815 の後、SELECTED 宣言 ~980-982 の後、`init_config` ~1085-1087 と ~1217-1221 の後、`@property` 群）
- Modify: `src-python/controller.py`（`_ENGINE_MODEL_BINDINGS` ~94-108、Ollama エンドポイント ~3168-3182 の後、`init()` の `check_translation_engine` の Ollama case ~4797 の後と結果反映 ~4853/~4897 の後、`shutdown()` ~466）
- Modify: `src-python/mainloop.py`（`run_mapping` ~115-116 の後、`mapping` ~341-345 の後）
- Modify: `src-python/test/test_controller_translation_connection_endpoints.py`（`_RUN_MAPPING` と AI_CLI 用クラス）
- Create: `src-python/test/test_controller_ai_cli.py`

**Interfaces:**
- Consumes: Task 6 の model メソッド・レジストリ・エラーコード
- Produces:
  - config: `SELECTABLE_AI_CLI_TOOL_LIST`, `SELECTED_AI_CLI_TOOL`, `SELECTABLE_AI_CLI_MODEL_LIST`, `SELECTED_AI_CLI_MODELS`, `SELECTED_AI_CLI_MODEL`（property）
  - controller: `getTranslatorAiCliConnection`, `checkTranslatorAiCliConnection`, `getTranslatorAiCliModelList`, `getTranslatorAiCliModel`, `setTranslatorAiCliModel`, `getSelectableAiCliToolList`, `getSelectedAiCliTool`, `setSelectedAiCliTool`, `_checkAiCliAtStartup() -> (status, model_list, selected_model)`
  - エンドポイント: `/get/data/connected_ai_cli`, `/run/ai_cli_connection`, `/get/data/selectable_ai_cli_model_list`, `/get/data/selected_ai_cli_model`, `/set/data/selected_ai_cli_model`, `/get/data/selectable_ai_cli_tool_list`, `/get/data/selected_ai_cli_tool`, `/set/data/selected_ai_cli_tool`
  - run_mapping: `selectable_ai_cli_model_list`, `selected_ai_cli_model`, `ai_cli_connection`

- [ ] **Step 1: 失敗するテストを書く**

`test_controller_translation_connection_endpoints.py` の `_RUN_MAPPING` に 2 行を足す:
```python
    "selectable_ai_cli_model_list": "/run/selectable_ai_cli_model_list",
    "selected_ai_cli_model": "/run/selected_ai_cli_model",
```
同ファイルの末尾（`if __name__` の前）に:
```python
class AiCliConnectionEndpointTests(_ConnectionEndpointTestMixin, unittest.TestCase):
    ENGINE_KEY = "AI_CLI"
    CHECK_METHOD = "checkTranslatorAiCliConnection"
    MODEL_METHOD = "setTranslatorAiCliModel"
    MODEL_LIST_ATTR = "SELECTABLE_AI_CLI_MODEL_LIST"
    MODEL_ATTR = "SELECTED_AI_CLI_MODEL"
    AUTHENTICATE_MOCK = "authenticationTranslatorAiCli"
    GET_MODEL_LIST_MOCK = "getTranslatorAiCliModelList"
    SET_MODEL_MOCK = "setTranslatorAiCliModel"
    UPDATE_CLIENT_MOCK = "updateTranslatorAiCliClient"
    EXPECTED_CONNECT_KWARGS = {}

    # SELECTED_AI_CLI_MODEL は CLI ごとのモデルを持つ辞書から算出する property なので、
    # 親クラスの「_SELECTED_X_MODEL を保存して戻す」ではなく辞書と CLI を保存して戻す。
    def setUp(self) -> None:
        self._orig_models = dict(config.SELECTED_AI_CLI_MODELS)
        self._orig_tool = config.SELECTED_AI_CLI_TOOL
        self._orig_status = dict(config._SELECTABLE_TRANSLATION_ENGINE_STATUS)
        self._orig_model_list = list(config.SELECTABLE_AI_CLI_MODEL_LIST)
        self.controller = Controller.__new__(Controller)
        self.controller.run_mapping = _RUN_MAPPING
        self.controller.run = lambda *a, **k: None
        self.controller.updateTranslationEngineAndEngineList = lambda: None

    def tearDown(self) -> None:
        config.SELECTABLE_TRANSLATION_ENGINE_STATUS = self._orig_status
        config.SELECTABLE_AI_CLI_MODEL_LIST = self._orig_model_list
        config.SELECTED_AI_CLI_TOOL = self._orig_tool
        config.SELECTED_AI_CLI_MODELS = self._orig_models
```
`_ConnectionEndpointTestMixin` の各テストが `getattr(config, f"_{self.MODEL_ATTR}")` を使っている箇所があれば、それらは `setUp`/`tearDown` だけであることを確かめる（テスト本体は `getattr(config, self.MODEL_ATTR)`）。本体にもあれば、その行を `getattr(config, self.MODEL_ATTR)` に置き換える。

`src-python/test/test_controller_ai_cli.py`:
```python
import unittest
from unittest.mock import MagicMock, patch

import controller as controller_module
import mainloop
from config import config
from controller import Controller
from errors import ErrorCode


class _ConfigGuard(unittest.TestCase):
    def setUp(self):
        self._orig = (config.SELECTED_AI_CLI_TOOL, dict(config.SELECTED_AI_CLI_MODELS),
                      list(config.SELECTABLE_AI_CLI_TOOL_LIST), list(config.SELECTABLE_AI_CLI_MODEL_LIST),
                      dict(config._SELECTABLE_TRANSLATION_ENGINE_STATUS))

    def tearDown(self):
        (config.SELECTED_AI_CLI_TOOL, config.SELECTED_AI_CLI_MODELS, config.SELECTABLE_AI_CLI_TOOL_LIST,
         config.SELECTABLE_AI_CLI_MODEL_LIST, config.SELECTABLE_TRANSLATION_ENGINE_STATUS) = self._orig


class AiCliConfigTests(_ConfigGuard):
    def test_selected_model_is_remembered_per_tool(self):
        config.SELECTED_AI_CLI_TOOL = "claude"
        config.SELECTED_AI_CLI_MODEL = "sonnet"
        config.SELECTED_AI_CLI_TOOL = "codex"
        config.SELECTED_AI_CLI_MODEL = "gpt-5.5"
        config.SELECTED_AI_CLI_TOOL = "claude"
        self.assertEqual(config.SELECTED_AI_CLI_MODEL, "sonnet")
        self.assertEqual(config.SELECTED_AI_CLI_MODELS["codex"], "gpt-5.5")

    def test_setting_none_clears_the_tool_model(self):
        config.SELECTED_AI_CLI_TOOL = "claude"
        config.SELECTED_AI_CLI_MODEL = None
        self.assertIsNone(config.SELECTED_AI_CLI_MODEL)

    def test_unknown_tool_is_rejected(self):
        with self.assertRaises(Exception):
            config.SELECTED_AI_CLI_TOOL = "notepad"


class AiCliEndpointTests(_ConfigGuard):
    def _controller(self):
        controller = Controller.__new__(Controller)
        controller.run_mapping = mainloop.run_mapping
        controller.run = MagicMock()
        controller.updateTranslationEngineAndEngineList = lambda: None
        return controller

    def test_endpoints_are_routed(self):
        for endpoint in ("/get/data/connected_ai_cli", "/run/ai_cli_connection",
                         "/get/data/selectable_ai_cli_model_list", "/get/data/selected_ai_cli_model",
                         "/set/data/selected_ai_cli_model", "/get/data/selectable_ai_cli_tool_list",
                         "/get/data/selected_ai_cli_tool", "/set/data/selected_ai_cli_tool"):
            with self.subTest(endpoint=endpoint):
                self.assertIn(endpoint, mainloop.mapping)
        for key in ("selectable_ai_cli_model_list", "selected_ai_cli_model", "ai_cli_connection"):
            self.assertIn(key, mainloop.run_mapping)

    def test_set_tool_switches_and_rechecks_connection(self):
        config.SELECTABLE_AI_CLI_TOOL_LIST = ["codex", "claude"]
        controller = self._controller()
        with patch.object(controller_module, "model") as mock_model:
            mock_model.authenticationTranslatorAiCli.return_value = True
            mock_model.getTranslatorAiCliModelList.return_value = ["gpt-5.5"]
            response = controller.setSelectedAiCliTool("codex")
        self.assertEqual(response, {"status": 200, "result": "codex"})
        self.assertEqual(config.SELECTED_AI_CLI_TOOL, "codex")
        mock_model.setTranslatorAiCliTool.assert_called_once_with("codex")
        self.assertEqual(config.SELECTABLE_AI_CLI_MODEL_LIST, ["gpt-5.5"])
        controller.run.assert_any_call(200, "/run/ai_cli_connection", True)

    def test_set_tool_rejects_tool_that_is_not_installed(self):
        config.SELECTABLE_AI_CLI_TOOL_LIST = ["claude"]
        config.SELECTED_AI_CLI_TOOL = "claude"
        controller = self._controller()
        with patch.object(controller_module, "model"):
            response = controller.setSelectedAiCliTool("agy")
        self.assertNotEqual(response["status"], 200)
        self.assertEqual(response["result"]["error_code"], ErrorCode.CONNECTION_AI_CLI_FAILED.value)
        self.assertEqual(config.SELECTED_AI_CLI_TOOL, "claude")

    def test_shutdown_closes_ai_cli_sessions(self):
        import inspect
        source = inspect.getsource(Controller.shutdown)
        self.assertIn("model.closeTranslatorAiCli", source)


if __name__ == "__main__":
    unittest.main()
```
`error_response["result"]` のキー名と `ErrorCode` が Enum かどうかは `errors.py` の `VRCTError.create_error_response` で確かめ、テストを実物に合わせる。

- [ ] **Step 2: 失敗を確認する**

Run: `.venv\Scripts\python -m pytest src-python/test/test_controller_ai_cli.py src-python/test/test_controller_translation_connection_endpoints.py -q`
Expected: FAIL（config 属性・controller メソッドが無い）

- [ ] **Step 3: config を足す**

Selectable 宣言（`SELECTABLE_OLLAMA_MODEL_LIST` の後）に:
```python
    SELECTABLE_AI_CLI_TOOL_LIST = ManagedProperty('SELECTABLE_AI_CLI_TOOL_LIST', type_=list, serialize=False, mutable_tracking=True)
    SELECTABLE_AI_CLI_MODEL_LIST = ManagedProperty('SELECTABLE_AI_CLI_MODEL_LIST', type_=list, serialize=False, mutable_tracking=True)
```
保存される宣言（`SELECTED_OLLAMA_MODEL` の後）に:
```python
    SELECTED_AI_CLI_TOOL = ManagedProperty('SELECTED_AI_CLI_TOOL', type_=str, allowed=lambda v, inst: v in AI_CLI_TOOLS)
    SELECTED_AI_CLI_MODELS = ManagedProperty('SELECTED_AI_CLI_MODELS', type_=dict)

    @property
    def SELECTED_AI_CLI_MODEL(self):
        """選んでいる CLI のモデル。CLI ごとの値は SELECTED_AI_CLI_MODELS に保存する。"""
        return self.SELECTED_AI_CLI_MODELS.get(self.SELECTED_AI_CLI_TOOL) or None

    @SELECTED_AI_CLI_MODEL.setter
    def SELECTED_AI_CLI_MODEL(self, value):
        models = dict(self.SELECTED_AI_CLI_MODELS)
        models[self.SELECTED_AI_CLI_TOOL] = value or ""
        self.SELECTED_AI_CLI_MODELS = models
```
`Config` クラスの定義より前（モジュールの定数が並ぶ場所）に次を足す。`models.translation.ai_cli.catalog` を config から import すると循環 import になり得るので、値を直接書く:
```python
# AI CLI の CLI 名。models/translation/ai_cli/catalog.py の TOOLS と同じ並び。
AI_CLI_TOOLS = ("codex", "claude", "agy")
```
`init_config` の `self._SELECTABLE_OLLAMA_MODEL_LIST = []` の後に:
```python
        self._SELECTABLE_AI_CLI_TOOL_LIST = []
        self._SELECTABLE_AI_CLI_MODEL_LIST = []
```
`self._SELECTED_OLLAMA_MODEL = None` の後に:
```python
        self._SELECTED_AI_CLI_TOOL = "claude"
        self._SELECTED_AI_CLI_MODELS = {"codex": "", "claude": "haiku", "agy": ""}
```
`load_config` が保存済みの `SELECTED_AI_CLI_MODELS` を読むとき、キーが欠けた辞書でも動くよう、getter の `.get()` で吸収している（追加の処理は不要）。

- [ ] **Step 4: controller を足す**

`_ENGINE_MODEL_BINDINGS` の `"Ollama"` の後に:
```python
    "AI_CLI": {
        "authenticate": "authenticationTranslatorAiCli",
        "get_model_list": "getTranslatorAiCliModelList",
        "set_model": "setTranslatorAiCliModel",
        "update_client": "updateTranslatorAiCliClient",
    },
```
Ollama のエンドポイントメソッドの後に:
```python
    def getTranslatorAiCliConnection(self, *args, **kwargs) -> dict:
        return {"status":200, "result":model.getTranslatorAiCliConnected()}

    def checkTranslatorAiCliConnection(self, *args, **kwargs) -> dict:
        return self._checkTranslationEngineConnection("AI_CLI", connect_kwargs={})

    def getTranslatorAiCliModelList(self, *args, **kwargs) -> dict:
        return self._getTranslationEngineModelList("AI_CLI")

    def getTranslatorAiCliModel(self, *args, **kwargs) -> dict:
        return self._getTranslationEngineModel("AI_CLI")

    def setTranslatorAiCliModel(self, data, *args, **kwargs) -> dict:
        return self._setTranslationEngineModel("AI_CLI", data)

    @staticmethod
    def getSelectableAiCliToolList(*args, **kwargs) -> dict:
        return {"status":200, "result":config.SELECTABLE_AI_CLI_TOOL_LIST}

    @staticmethod
    def getSelectedAiCliTool(*args, **kwargs) -> dict:
        return {"status":200, "result":config.SELECTED_AI_CLI_TOOL}

    def setSelectedAiCliTool(self, data, *args, **kwargs) -> dict:
        tool = str(data)
        if tool not in config.SELECTABLE_AI_CLI_TOOL_LIST:
            return VRCTError.create_error_response(
                ErrorCode.CONNECTION_AI_CLI_FAILED,
                data=config.SELECTED_AI_CLI_TOOL,
            )
        config.SELECTED_AI_CLI_TOOL = tool
        model.setTranslatorAiCliTool(tool)
        # CLI を替えたらモデル一覧と選択モデルを取り直し、接続状態を UI に知らせる。
        result = self._checkTranslationEngineConnection("AI_CLI", connect_kwargs={})
        self.run(200, self.run_mapping["ai_cli_connection"], result.get("status") == 200)
        return {"status":200, "result":config.SELECTED_AI_CLI_TOOL}
```
`setSelectedAiCliTool` の後に、起動時の確認を 1 つのメソッドにまとめて足す（`init()` の入れ子関数の中では単体テストできないため）:
```python
    @staticmethod
    def _checkAiCliAtStartup() -> tuple:
        """起動時の AI CLI の確認。(status, model_list, selected_model) を返す。

        CLI が 1 つも無ければ使えない扱いにする (init() の `case _:` に落ちると
        ネット接続だけで使える扱いになってしまう)。
        """
        tools = model.getTranslatorAiCliInstalledTools()
        config.SELECTABLE_AI_CLI_TOOL_LIST = tools
        if not tools:
            return False, None, None
        if config.SELECTED_AI_CLI_TOOL not in tools:
            config.SELECTED_AI_CLI_TOOL = tools[0]
        if model.authenticationTranslatorAiCli() is not True:
            return False, None, None
        model_list = model.getTranslatorAiCliModelList()
        if len(model_list) == 0:
            return False, model_list, None
        selected = config.SELECTED_AI_CLI_MODEL if config.SELECTED_AI_CLI_MODEL in model_list else model_list[0]
        return True, model_list, selected
```
`init()` の `check_translation_engine` の `case "Ollama":` の後（`case _:` の前）に:
```python
                    case "AI_CLI":
                        status, model_list, selected_model = Controller._checkAiCliAtStartup()
```
結果反映の `if engine == "Ollama" and not status:` の後に:
```python
            if engine == "AI_CLI" and not status:
                config.SELECTABLE_AI_CLI_MODEL_LIST = []
```
モデルリスト反映の `case "Ollama":` の後に:
```python
                    case "AI_CLI":
                        config.SELECTABLE_AI_CLI_MODEL_LIST = model_list
                        config.SELECTED_AI_CLI_MODEL = selected_model
                        model.setTranslatorAiCliModel(selected_model)
                        # 常駐セッションは、AI CLI がどこかのタブで翻訳エンジンに選ばれているときだけ起動しておく。
                        if "AI_CLI" in config.SELECTED_TRANSLATION_ENGINES.values():
                            model.updateTranslatorAiCliClient()
```
`shutdown()` の `self._stopServiceForShutdown(model.stopTranslationExecutor, "translation executor")` の直前に:
```python
        self._stopServiceForShutdown(model.closeTranslatorAiCli, "AI CLI sessions")
```

- [ ] **Step 5: mainloop を足す**

`run_mapping` の `"selected_ollama_model":...` の後に:
```python
    "selectable_ai_cli_model_list":"/run/selectable_ai_cli_model_list",
    "selected_ai_cli_model":"/run/selected_ai_cli_model",
    "ai_cli_connection":"/run/ai_cli_connection",
```
`mapping` の `"/set/data/selected_ollama_model"` の後に:
```python
    "/get/data/connected_ai_cli": {"status": True, "variable":controller.getTranslatorAiCliConnection},
    "/run/ai_cli_connection": {"status": True, "variable":controller.checkTranslatorAiCliConnection},
    "/get/data/selectable_ai_cli_model_list": {"status": True, "variable":controller.getTranslatorAiCliModelList},
    "/get/data/selected_ai_cli_model": {"status": True, "variable":controller.getTranslatorAiCliModel},
    "/set/data/selected_ai_cli_model": {"status": True, "variable":controller.setTranslatorAiCliModel},
    "/get/data/selectable_ai_cli_tool_list": {"status": True, "variable":controller.getSelectableAiCliToolList},
    "/get/data/selected_ai_cli_tool": {"status": True, "variable":controller.getSelectedAiCliTool},
    "/set/data/selected_ai_cli_tool": {"status": True, "variable":controller.setSelectedAiCliTool},
```

- [ ] **Step 6: 起動時のテストを足す**

`test_controller_ai_cli.py` の末尾（`if __name__` の前）に:
```python
class AiCliInitTests(_ConfigGuard):
    def test_init_has_an_explicit_ai_cli_case(self):
        # case が無いと既定の `case _: status = connected_network is True` に落ち、
        # CLI が無い PC でもネット接続だけで「使える」扱いになる。
        import inspect
        self.assertIn('case "AI_CLI":', inspect.getsource(Controller.init))

    def test_init_marks_ai_cli_unavailable_when_no_cli_installed(self):
        with patch.object(controller_module, "model") as mock_model:
            mock_model.getTranslatorAiCliInstalledTools.return_value = []
            self.assertEqual(Controller._checkAiCliAtStartup(), (False, None, None))
        mock_model.authenticationTranslatorAiCli.assert_not_called()
        self.assertEqual(config.SELECTABLE_AI_CLI_TOOL_LIST, [])

    def test_startup_switches_to_an_installed_tool_and_keeps_saved_model(self):
        config.SELECTED_AI_CLI_TOOL = "claude"
        config.SELECTED_AI_CLI_MODELS = {"codex": "gpt-6-sol", "claude": "haiku", "agy": ""}
        with patch.object(controller_module, "model") as mock_model:
            mock_model.getTranslatorAiCliInstalledTools.return_value = ["codex"]
            mock_model.authenticationTranslatorAiCli.return_value = True
            mock_model.getTranslatorAiCliModelList.return_value = ["gpt-5.5", "gpt-6-sol"]
            self.assertEqual(Controller._checkAiCliAtStartup(), (True, ["gpt-5.5", "gpt-6-sol"], "gpt-6-sol"))
        self.assertEqual(config.SELECTED_AI_CLI_TOOL, "codex")

    def test_startup_falls_back_to_first_model_when_saved_one_is_gone(self):
        config.SELECTED_AI_CLI_TOOL = "codex"
        config.SELECTED_AI_CLI_MODELS = {"codex": "old-model", "claude": "haiku", "agy": ""}
        with patch.object(controller_module, "model") as mock_model:
            mock_model.getTranslatorAiCliInstalledTools.return_value = ["codex"]
            mock_model.authenticationTranslatorAiCli.return_value = True
            mock_model.getTranslatorAiCliModelList.return_value = ["gpt-5.5"]
            self.assertEqual(Controller._checkAiCliAtStartup(), (True, ["gpt-5.5"], "gpt-5.5"))
```
`init()` 全体を動かす既存テスト（`test_controller_init_*.py`）に、エンジン一覧を固定値で持つものがあれば `AI_CLI` を足し、`model.getTranslatorAiCliInstalledTools` のモックを追加する。

- [ ] **Step 7: テストが通ることを確認する**

Run: `.venv\Scripts\python -m pytest -q`
Expected: 失敗なし（`test_translation_providers.py` の AI_CLI 関連も通る）

- [ ] **Step 8: Commit**

```powershell
git add src-python/
git commit -m "feat(translation): AI CLI の設定・エンドポイント・起動時の確認・終了処理を追加"
```

---

### Task 8: UI

**Files:**
- Modify: `src-ui/logics/ui_configs.js`（`translator_status` の `Ollama` の後）
- Modify: `src-ui/logics/configs/config_page_setter/ui_config_setter.js`（Ollama ブロック ~439-466 の後）
- Modify: `src-ui/logics/store.js`（~164 の後）
- Modify: `src-ui/logics/common/useLLMConnection.js`
- Modify: `src-ui/logics/useReceiveRoutes.js`（~29-30 の後）
- Modify: `src-ui/logics/_useBackendErrorHandling.js`（~64, ~86-89, ~269-284）
- Modify: `src-ui/views/app/config_page/setting_section/setting_box/translation/Translation.jsx`（~57-58 の後、~593 の後）
- Modify: `locales/ja.yml`, `locales/en.yml`, `locales/ko.yml`, `locales/zh-Hans.yml`, `locales/zh-Hant.yml`（`select_ollama_model:` ブロックの後）

**Interfaces:**
- Consumes: Task 7 のエンドポイントと run ルート
- Produces: `useTranslation()` に `currentSelectableAiCliToolList`, `currentSelectedAiCliTool`, `setSelectedAiCliTool`, `currentSelectableAiCliModelList`, `currentSelectedAiCliModel`, `setSelectedAiCliModel`, `updateSelectedAiCliModel`（設定宣言からの自動生成）。`useLLMConnection()` に `currentIsAiCliConnected`, `updateIsAiCliConnected`, `setConnectionStatus_AiCli`, `checkConnection_AiCli`。

- [ ] **Step 1: 宣言とストアを足す**

`ui_configs.js` の `{ id: "Ollama", ... },` の後に:
```js
    { id: "AI_CLI", label: `AI CLI`, is_available: false },
```
`ui_config_setter.js` の `SelectedOllamaModel` 宣言の後に:
```js
    // AI CLI
    {
        Category: "Translation",
        Base_Name: "SelectableAiCliToolList",
        default_value: [],
        ui_template_id: "list",
        logics_template_id: "get_set",
        base_endpoint_name: "selectable_ai_cli_tool_list",
        response_transform: "arrayToObject",
    },
    {
        Category: "Translation",
        Base_Name: "SelectedAiCliTool",
        default_value: "",
        ui_template_id: "select",
        logics_template_id: "get_set",
        base_endpoint_name: "selected_ai_cli_tool",
    },
    {
        Category: "Translation",
        Base_Name: "SelectableAiCliModelList",
        default_value: [],
        ui_template_id: "list",
        logics_template_id: "get_set",
        add_endpoint_run_array: ["from_backend"],
        base_endpoint_name: "selectable_ai_cli_model_list",
        response_transform: "arrayToObject",
    },
    {
        Category: "Translation",
        Base_Name: "SelectedAiCliModel",
        default_value: "",
        ui_template_id: "select",
        logics_template_id: "get_set",
        add_endpoint_run_array: ["from_backend"],
        base_endpoint_name: "selected_ai_cli_model",
    },
```
`store.js` の `Atom_IsOllamaConnected` の行の後に:
```js
export const { atomInstance: Atom_IsAiCliConnected, useHook: useStore_IsAiCliConnected } = createAtomWithHook(false, "IsAiCliConnected");
```

- [ ] **Step 2: 接続状態の配線**

`useLLMConnection.js`: import に `useStore_IsAiCliConnected,` を足し、Ollama と同じ形で:
```js
    const {
        currentIsAiCliConnected,
        updateIsAiCliConnected,
        pendingIsAiCliConnected,
    } = useStore_IsAiCliConnected();
```
```js
    const checkConnection_AiCli = () => {
        pendingIsAiCliConnected();
        asyncStdoutToPython("/run/ai_cli_connection");
    };
    const setConnectionStatus_AiCli = (is_connected) => {
        updateIsAiCliConnected(is_connected);
    };
```
return に `currentIsAiCliConnected, updateIsAiCliConnected, setConnectionStatus_AiCli, checkConnection_AiCli,` を足す。

`useReceiveRoutes.js` の Ollama の 2 行の後に:
```js
    { endpoint: "/get/data/connected_ai_cli", ns: common, hook_name: "useLLMConnection", method_name: "setConnectionStatus_AiCli" },
    { endpoint: "/run/ai_cli_connection", ns: common, hook_name: "useLLMConnection", method_name: "setConnectionStatus_AiCli" },
```

`_useBackendErrorHandling.js`: `useTranslation()` の分割代入に `updateSelectedAiCliModel,`、`useLLMConnection()` の分割代入に `updateIsAiCliConnected,` を足し、Ollama の case の後に:
```js
            case "MODEL_AI_CLI_INVALID":
                updateSelectedAiCliModel(data);
                showNotification_Error(message, { category_id: error_code });
                return;
```
```js
            case "CONNECTION_AI_CLI_FAILED":
                updateIsAiCliConnected(false);
                showNotification_Error(message, { category_id: error_code });
                return;
```
（`CONNECTION_AI_CLI_FAILED` は CLI 切り替えの拒否でも返り、そのときの `data` は CLI 名なので、接続状態には `data` ではなく `false` を入れる。）

- [ ] **Step 3: 設定画面に出す**

`Translation.jsx` の `<OllamaModelContainer />` の後に:
```jsx

            <AiCliTool_Box />
            <AiCliConnectionCheck_Box />
            <AiCliModelContainer />
```
`OllamaModelContainer` の定義の後に:
```jsx
const AiCliTool_Box = () => {
    const { t } = useI18n();
    const {
        currentSelectableAiCliToolList,
        currentSelectedAiCliTool,
        setSelectedAiCliTool,
    } = useTranslation();

    const is_empty = Object.keys(currentSelectableAiCliToolList.data ?? {}).length === 0;
    const selected_label = is_empty
        ? t("config_page.translation.ai_cli_tool.not_installed")
        : currentSelectedAiCliTool.data;

    return (
        <DropdownMenuContainer
            dropdown_id="select_ai_cli_tool"
            label={t("config_page.translation.ai_cli_tool.label")}
            desc={t("config_page.translation.ai_cli_tool.desc")}
            selected_id={selected_label}
            list={currentSelectableAiCliToolList.data}
            selectFunction={(selected_data) => setSelectedAiCliTool(selected_data.selected_id)}
            state={currentSelectedAiCliTool.state}
            is_disabled={is_empty}
        />
    );
};
const AiCliConnectionCheck_Box = () => {
    const { t } = useI18n();
    const { currentIsAiCliConnected, checkConnection_AiCli } = useLLMConnection();

    return (
        <ConnectionCheckButtonContainer
            label={t("config_page.translation.ai_cli_connection_check.label")}
            variable={currentIsAiCliConnected.data}
            state={currentIsAiCliConnected.state}
            checkFunction={checkConnection_AiCli}
            remove_border_bottom={true}
        />
    );
};
const AiCliModelContainer = () => {
    const { t } = useI18n();
    const {
        currentSelectableAiCliModelList,
        currentSelectedAiCliModel,
        setSelectedAiCliModel,
    } = useTranslation();
    const { currentIsAiCliConnected } = useLLMConnection();

    const selected_label = (!currentIsAiCliConnected.data && !currentSelectedAiCliModel.data)
        ? t("config_page.translation.select_ai_cli_model.connection_required")
        : currentSelectedAiCliModel.data;

    return (
        <DropdownMenuContainer
            dropdown_id="select_ai_cli_model"
            label={t("config_page.translation.select_ai_cli_model.label")}
            selected_id={selected_label}
            list={currentSelectableAiCliModelList.data}
            selectFunction={(selected_data) => setSelectedAiCliModel(selected_data.selected_id)}
            state={currentSelectedAiCliModel.state}
            is_disabled={!currentIsAiCliConnected.data}
        />
    );
};
```
`DropdownMenuContainer` が `desc` を受け取れるかを Templates.jsx で確かめ、受け取れなければ `desc` を渡さない。

- [ ] **Step 4: 文言を足す**

`locales/ja.yml` の `select_ollama_model:` ブロックの後（同じ 8 スペースのインデント）に:
```yaml
        ai_cli_tool:
            label: "AI CLI"
            desc: "この PC に入っている AI の CLI（codex / claude / agy）で翻訳します。CLI にログインしておく必要があります。"
            not_installed: "対応する CLI が見つかりません"
        ai_cli_connection_check:
            label: "AI CLI の接続確認"
        select_ai_cli_model:
            label: "AI CLI のモデルを選択"
            connection_required: "AI CLI の接続確認が必要"
```
`locales/en.yml`（ko / zh-Hans / zh-Hant も同じ英語）に:
```yaml
        ai_cli_tool:
            label: "AI CLI"
            desc: "Translate with an AI CLI installed on this PC (codex / claude / agy). You need to be logged in to the CLI."
            not_installed: "No supported CLI found"
        ai_cli_connection_check:
            label: "Check AI CLI Connection"
        select_ai_cli_model:
            label: "Select AI CLI Model"
            connection_required: "AI CLI Connection Check Required"
```

- [ ] **Step 5: 確認**

```powershell
npm run vite-build
.venv\Scripts\python -m pytest src-python/test/test_ui_endpoint_contract.py -q
foreach ($f in 'ja','en','ko','zh-Hans','zh-Hant') { .venv\Scripts\python -c "import yaml,sys; d=yaml.safe_load(open(r'locales\$f.yml',encoding='utf-8')); t=d['config_page']['translation']; print('$f', all(k in t for k in ('ai_cli_tool','ai_cli_connection_check','select_ai_cli_model')))" }
```
Expected: ビルド成功、契約テスト passed、5 ファイルとも True。

- [ ] **Step 6: Commit**

```powershell
git add src-ui/ locales/
git commit -m "feat(ui): 翻訳の設定に AI CLI（CLI とモデルの選択・接続確認）を追加"
```

---

### Task 9: 実際の CLI での確認

**Files:**
- Modify: `docs/perf/README.md`（末尾に短い追記。計測ではなく動作確認の記録）

**Interfaces:**
- Consumes: すべての前タスク
- Produces: 実 CLI での確認結果

- [ ] **Step 1: 全テスト**

Run: `.venv\Scripts\python -m pytest -q`
Expected: 失敗なし

- [ ] **Step 2: サイドカーでの確認（UI なし）**

`bat\build.bat` でサイドカーを作り、プロジェクトスキル `run-vrct` の `drive_sidecar.py` と同じプロトコルで次を送るスクリプトを一時フォルダに作って実行する（リポジトリには入れない）:
1. `/run/initialization_complete` を待つ
2. `/get/data/selectable_ai_cli_tool_list` を読む（入っている CLI が並ぶこと）
3. CLI ごとに: `/set/data/selected_ai_cli_tool` → 応答と `/run/ai_cli_connection` の True を待つ → `/set/data/selected_translation_engines` で タブ 1 のエンジンを `AI_CLI` に → `/set/enable/translation` → `/run/send_message_box` で「こんにちは、元気ですか？」を送り、訳文と所要時間を記録 → もう 1 通送り、2 回目の所要時間を記録
4. 最後に翻訳エンジンを `CTranslate2` に戻し、翻訳をオフに戻す

`/set/data/selected_translation_engines` の data の形は `config.SELECTED_TRANSLATION_ENGINES`（タブ番号 → エンジン名の辞書）を `/get/data/selected_translation_engines` で読んでから書き換えて送る。

Expected: 3 つの CLI とも英訳が返る。2 回目は claude で数秒以内、agy・codex で 10 秒以内。サイドカーを終えたあと、`Get-Process claude, agy, node -ErrorAction SilentlyContinue` で起動したプロセスが残っていないこと（開始前の一覧と比べる）。

- [ ] **Step 3: 画面での確認**

`npm run dev-ui` で起動し、`run-vrct` スキルの `act.ps1` / `shot.ps1` で次を確かめる:
- 翻訳の設定に「AI CLI」「AI CLI の接続確認」「AI CLI のモデルを選択」が出る
- CLI を切り替えるとモデル一覧が変わる
- メイン画面の翻訳エンジンに「AI CLI」が選べ、チャットの翻訳が表示される

- [ ] **Step 4: 記録と Commit**

`docs/perf/README.md` の末尾に「追記（AI CLI 翻訳）」として、CLI ごとの 1 回目・2 回目の所要時間と、確認できなかった項目を書く。
```powershell
git add docs/perf/README.md
git commit -m "docs: AI CLI 翻訳の実機確認の結果を記録"
```

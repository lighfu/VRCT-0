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

"""AI CLI を常駐させる共通部分。

CLI を翻訳のたびに起動すると 1 回 10〜100 秒かかる (2026-09-24 実測) ため、
1 本のプロセスを起動したままにして 1 行 1 JSON でターンを送る。
CLI ごとの違い (起動引数・送る JSON・完了の見分け方) はサブクラスが持つ。

`close()` は `translate()` が握る `_lock` を待たずに割り込めなければならない
(シャットダウン時に固まった CLI を待ち続けると、呼び出し元ごとタイムアウトする)。
そのため `_proc` の入れ替えだけを守る `_procLock` を別に持ち、実際にプロセスを
kill する処理はどちらのロックも保持しないところで行う。加えて各ターン (と
起動直後のハンドシェイク) には `threading.Timer` の見張り役を付け、
`proc.stdin.write()` がブロックしたままでも締め切りで確実に殺せるようにする。

`close()` と `shutdown()` は別物: `close()` は「今のプロセスを殺すだけ」で、
次の `translate()` は普通に再起動する。`shutdown()` は恒久的にセッションを
無効化し、以後の `start()`/`translate()` はすべて `AiCliError` になる
(呼び出し元がセッションへの参照を手放した後でも、既に走っていた
起動中スレッドが CLI を孤児として残さないようにするため)。

`start()` の途中 (Popen 成功後・`_proc` 公開前) に別スレッドから `close()`/
`shutdown()` が割り込むと、公開されないまま孤児プロセスが残ってしまう。これを
防ぐため `_generation` を持ち、`close()`/`shutdown()` は `_procLock` の下で
これをインクリメントする。`start()` は自分が観測した世代を覚えておき、
`_spawn()` は公開直前に世代 (と shutdown フラグ) を `_procLock` の下で
再確認し、ずれていれば今作ったプロセスを黙って殺して `AiCliError` を送出する。
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
        self._procLock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._messages: "queue.Queue" = queue.Queue()
        self._stderr: deque = deque(maxlen=50)
        self._turns = 0
        self._generation = 0
        self._shut_down = False

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

    def _beforePublish(self, proc: subprocess.Popen) -> None:
        """テスト用フック: Popen 成功後・`_proc` 公開前に呼ばれる。既定は何もしない。

        close()/_spawn の競合をテストで決定的に再現するためのもの。
        """

    # --- 公開メソッド ---
    def isAlive(self) -> bool:
        with self._procLock:
            proc = self._proc
        return proc is not None and proc.poll() is None

    def start(self) -> None:
        with self._lock:
            if self.isAlive():
                return
            with self._procLock:
                if self._shut_down:
                    raise AiCliError("session was shut down")
                generation = self._generation
            proc = self._spawn(generation)
            watchdog = None
            if proc is not None:
                watchdog = threading.Timer(self.START_TIMEOUT, self._killIfStill, args=(proc,))
                watchdog.daemon = True
                watchdog.start()
            try:
                self._afterStart(time.monotonic() + self.START_TIMEOUT)
            except Exception:
                self.close()
                raise
            finally:
                if watchdog is not None:
                    watchdog.cancel()
                    watchdog.join()

    def translate(self, prompt: str, timeout: Optional[float] = None) -> str:
        with self._lock:
            try:
                if self.isAlive() and self._turns >= self.MAX_TURNS:
                    self.close()
                if not self.isAlive():
                    self.start()
                self._drainStale()
                if not self.isAlive():
                    self.start()
                proc = self._proc
                # 起動直後の最初のターンはモデルの準備 (claude で約 30 秒) を含むので長めに待つ。
                limit = self.START_TIMEOUT if self._turns == 0 else (timeout or self.TURN_TIMEOUT)
                deadline = time.monotonic() + limit
                # proc.stdin.write() 自体は締め切りで自動的には止まらないので、
                # 別スレッドの見張り役でターン全体 (書き込みも含む) を締め切りに縛る。
                watchdog = None
                if proc is not None:
                    watchdog = threading.Timer(limit, self._killIfStill, args=(proc,))
                    watchdog.daemon = True
                    watchdog.start()
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
                finally:
                    # 見張り役が発火した直後で cancel() が効かない場合でも、
                    # join() で完全に終わるのを待ってから次のターンに進む。
                    # そうしないと、既に完了した見張り役が次のターンの
                    # (別の) 健全なプロセスを誤って殺す隙が生まれる。
                    if watchdog is not None:
                        watchdog.cancel()
                        watchdog.join()
            except AiCliError:
                self.close()
                raise
            except Exception as e:
                self.close()
                raise AiCliError(str(e)) from e

    def close(self) -> None:
        """今のプロセスを殺す。shutdown 済みでなければ次の translate() で再起動される。"""
        with self._procLock:
            self._generation += 1
            proc = self._proc
            self._proc = None
        if proc is not None:
            self._kill(proc)

    def shutdown(self) -> None:
        """セッションを恒久的に終える。以後 start()/translate() は AiCliError を送出する。"""
        with self._procLock:
            self._shut_down = True
            self._generation += 1
            proc = self._proc
            self._proc = None
        if proc is not None:
            self._kill(proc)

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

    # --- プロセスの入れ替え・停止 (self._lock を握らずに呼べる) ---
    def _detachIfCurrent(self, proc: subprocess.Popen) -> Optional[subprocess.Popen]:
        """`_proc` が `proc` と同一のときだけ切り離して返す (世代は変えない)。

        見張り役タイマー専用。close()/shutdown() による本物の世代交代とは違い、
        既に別プロセスに交代済みなら何もしない (誤って新しいプロセスを
        殺さないため)。
        """
        with self._procLock:
            if self._proc is proc:
                self._proc = None
                return proc
            return None

    def _killIfStill(self, proc: Optional[subprocess.Popen]) -> None:
        """締め切りを過ぎたときに見張り役タイマーから呼ばれる。"""
        if proc is None:
            return
        detached = self._detachIfCurrent(proc)
        if detached is not None:
            self._kill(detached)

    @staticmethod
    def _kill(proc: subprocess.Popen) -> None:
        """OS プロセス (と Windows での子プロセス) を確実に止める。例外は握りつぶす。

        `proc.stdin` を先に `close()` してはいけない: もし別スレッドが
        `proc.stdin.write()` の OS 呼び出しでブロックしていると、`close()` は
        その書き込みスレッドが握っている内部バッファのロックを待ってしまい、
        ここ自体が止まって taskkill にたどり着けなくなる (実測で確認済み)。
        先にプロセスを殺せば、詰まっていた書き込みは broken pipe で解放される。
        それでも (taskkill が失敗し、なおかつ読み手が生き残っている等で)
        stdin の close 自体がブロックする可能性は残るので、close() は
        デーモンスレッドに投げっぱなしにして `_kill()` 自体が絶対に
        ブロックしないようにする。
        """
        try:
            if proc.poll() is None:
                if os.name == "nt":
                    system_root = os.environ.get("SystemRoot", r"C:\Windows")
                    taskkill = os.path.join(system_root, "System32", "taskkill.exe")
                    if not os.path.isfile(taskkill):
                        taskkill = "taskkill"
                    # codex.cmd -> node のように子プロセスがいるので木ごと止める。
                    subprocess.run([taskkill, "/T", "/F", "/PID", str(proc.pid)],
                                    capture_output=True, timeout=5, creationflags=_CREATE_NO_WINDOW)
                else:
                    proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
        try:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=2)
        except Exception:
            pass
        CliSession._closeStdinAsync(proc)

    @staticmethod
    def _closeStdinAsync(proc: subprocess.Popen) -> None:
        stdin = proc.stdin
        if stdin is None:
            return

        def _close() -> None:
            try:
                stdin.close()
            except Exception:
                pass

        threading.Thread(target=_close, daemon=True).start()

    # --- 内部 ---
    def _spawn(self, generation: int) -> Optional[subprocess.Popen]:
        """新しいプロセスを起動する。

        起動中 (Popen の呼び出し中) に別スレッドが close()/shutdown() すると
        `_generation` が進むので、Popen 完了後にそれを確認し、ずれていたら
        (または既に shutdown 済みなら) 今作ったプロセスを黙って殺して
        AiCliError を送出する。呼び出し元にはプロセスを公開できたときだけ
        proc を返す。
        """
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

        self._beforePublish(proc)

        messages: "queue.Queue" = queue.Queue()
        stderr: deque = deque(maxlen=50)
        with self._procLock:
            shut_down = self._shut_down
            stale = self._generation != generation
            if shut_down or stale:
                published = False
            else:
                self._proc = proc
                published = True

        if not published:
            self._kill(proc)
            if shut_down:
                raise AiCliError("session was shut down")
            raise AiCliError("AI CLI session was closed while starting")

        self._messages = messages
        self._stderr = stderr
        self._turns = 0
        threading.Thread(target=self._readStdout, args=(proc, messages), daemon=True).start()
        threading.Thread(target=self._readStderr, args=(proc, stderr), daemon=True).start()
        return proc

    def _drainStale(self) -> None:
        """次のターンを送る前に、前のターンの取りこぼしをためずに捨てる。

        途中で _EOF (プロセス終了の印) を見つけたら、古いプロセスを片付けて
        新しく起動し直す。
        """
        saw_eof = False
        while True:
            try:
                item = self._messages.get_nowait()
            except queue.Empty:
                break
            if item is _EOF:
                saw_eof = True
        if saw_eof:
            self.close()
            self.start()

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

    @staticmethod
    def _readStderr(proc: subprocess.Popen, stderr: deque) -> None:
        try:
            for line in proc.stderr:
                stderr.append(line.rstrip())
        except Exception:
            pass

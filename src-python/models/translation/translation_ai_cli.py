"""翻訳エンジン「AI CLI」: ユーザーの PC にある codex / claude / agy を常駐させて翻訳する。

CLI ごとに常駐セッションを 1 本持つ。CLI かモデルを変えたらセッションを閉じ、
次の翻訳か updateClient() (裏で起動しておく) で作り直す。入力・出力言語は
翻訳ごとに変わるので、指示は毎回のプロンプトに含める。

close() は session.shutdown() (恒久停止) を呼ぶ。session.close() (再起動可能な kill)
だと、既に古いセッションを掴んでいる warm-up スレッドがそれを生き返らせてしまい、
孤児プロセスが残る可能性があるため。

モデル一覧は CLI ごとに覚えておく (`codex debug models` / `agy models` は数秒かかる)。
一覧を問い合わせ直すのは接続確認と CLI の切り替えのときだけで、setModel() は
覚えている一覧で確かめる。

サーキットブレーカー: CLI の起動か最初のターンが失敗したら (未ログイン・CLI の故障など)、
しばらく CLI を呼ばずに翻訳を即座に失敗させる (呼び出し元は CTranslate2 に切り替わる)。
その間はメッセージごとに CLI の起動を待たない。時間が過ぎたら裏で確認のターンを送り、
通れば元に戻す。状態が変わったら setStatusCallback() で登録した関数に知らせる
(UI の「接続済み」表示を実際の状態に合わせるため)。
"""

import os
import threading
import time
from typing import Callable, Optional

try:
    from .ai_cli import catalog
    from .ai_cli.agy_session import AgySession
    from .ai_cli.base import AiCliError, AiCliToolUseError
    from .ai_cli.claude_session import ClaudeSession
    from .ai_cli.codex_session import CodexSession
    from .translation_languages import translation_lang
    from .translation_llm_common import buildSystemPrompt
    from .translation_utils import loadTranslatePromptConfig
    from utils import errorLogging
except ImportError:
    import sys
    from os import path as os_path
    sys.path.append(os_path.dirname(os_path.dirname(os_path.dirname(os_path.abspath(__file__)))))
    from models.translation.ai_cli import catalog
    from models.translation.ai_cli.agy_session import AgySession
    from models.translation.ai_cli.base import AiCliError, AiCliToolUseError
    from models.translation.ai_cli.claude_session import ClaudeSession
    from models.translation.ai_cli.codex_session import CodexSession
    from models.translation.translation_languages import translation_lang
    from models.translation.translation_llm_common import buildSystemPrompt
    from models.translation.translation_utils import loadTranslatePromptConfig
    from utils import errorLogging

SESSION_CLASSES = {"codex": CodexSession, "claude": ClaudeSession, "agy": AgySession}
BASE_INSTRUCTIONS = "You are a translation engine. Output only the translation of the given text."
# 起動の確認に送る短いターン。ログインしていない・CLI が壊れているなどを、
# 最初のメッセージを待たずに見つける (実機ではどの CLI でも数秒で返る)。
PROBE_PROMPT = "Translate the following text into English. Output only the translation.\n\nText to translate:\nOK"


class AICliClient:
    # 起動か最初のターンが失敗してから、CLI を呼ばずに即座に失敗させる時間。
    BREAKER_SECONDS = 60.0

    def __init__(self, root_path: Optional[str] = None, workspace: Optional[str] = None,
                 client_version: str = "") -> None:
        prompt_config = loadTranslatePromptConfig(root_path, "translation_ai_cli.yml")
        self.prompt_template = prompt_config["system_prompt"]
        self.history_cfg = prompt_config.get("history", {"use_history": False})
        self.supported_languages = list(translation_lang["AI_CLI"]["source"].keys())
        self.workspace = workspace or os.path.join(root_path or ".", "ai_cli_workspace")
        self.client_version = client_version
        self.tool: Optional[str] = None
        self.model: Optional[str] = None
        self._context_history: list[dict] = []
        self._session = None
        self._shut_down = False
        self._lock = threading.Lock()
        self._models: dict[str, list[str]] = {}
        self._status_callback: Optional[Callable[[bool], None]] = None
        # CLI ごとに選んだエフォートを返す関数 (config を読む)。セッションを起動するたびに読む。
        self._effort_provider: Optional[Callable[[str], Optional[str]]] = None
        # codex の Fast モードを使うかを返す関数 (config を読む)。
        self._fast_provider: Optional[Callable[[], bool]] = None
        self._broken_reason: Optional[str] = None
        self._retry_at = 0.0
        self._probing = False

    # --- CLI とモデル ---
    def getInstalledTools(self) -> list[str]:
        return catalog.detectInstalledTools()

    def getTool(self) -> Optional[str]:
        return self.tool

    def setTool(self, tool: str) -> bool:
        if tool not in self.getInstalledTools():
            return False
        old = None
        with self._lock:
            if tool != self.tool:
                self.tool = tool
                self.model = None
                old, self._session = self._session, None
        if old is not None:
            old.shutdown()
        return True

    def getModelList(self) -> list[str]:
        """CLI にモデル一覧を問い合わせ直し、CLI ごとに覚えておく。

        問い合わせに失敗したら (一時的な通信エラーなど)、前に覚えた一覧を返す。
        """
        tool = self.tool
        if not tool:
            return []
        models = catalog.listModels(tool)
        with self._lock:
            if models:
                self._models[tool] = list(models)
                return list(models)
            return list(self._models.get(tool, []))

    def getModel(self) -> Optional[str]:
        return self.model

    def setModel(self, model: str) -> bool:
        """覚えているモデル一覧で確かめて選ぶ。一覧をまだ覚えていないときだけ問い合わせる。"""
        with self._lock:
            tool = self.tool
            known = self._models.get(tool) if tool else None
        if tool and known is None:
            known = self.getModelList()
        if not tool or model not in (known or []):
            return False
        old = None
        with self._lock:
            if self.tool != tool:
                # 確かめている間に CLI が切り替わった。
                return False
            if model != self.model:
                self.model = model
                old, self._session = self._session, None
        if old is not None:
            old.shutdown()
        return True

    def setEffortProvider(self, provider: Optional[Callable[[str], Optional[str]]]) -> None:
        self._effort_provider = provider

    def getEffortList(self) -> list[str]:
        """今の CLI とモデルで選べるエフォート。選べないなら []。"""
        if not self.tool or not self.model:
            return []
        return catalog.listEfforts(self.tool, self.model)

    def getEffort(self) -> Optional[str]:
        """実際に使うエフォート (選んでいた値がこのモデルで使えなければ low か一覧の先頭)。"""
        preferred = None
        if self._effort_provider is not None and self.tool:
            try:
                preferred = self._effort_provider(self.tool)
            except Exception:
                errorLogging()
        return catalog.effectiveEffort(preferred, self.getEffortList())

    def setFastProvider(self, provider: Optional[Callable[[], bool]]) -> None:
        self._fast_provider = provider

    def isFastAvailable(self) -> bool:
        """今の CLI とモデルで Fast モードを選べるか (codex で、モデルに Fast がある)。"""
        if not self.tool or not self.model:
            return False
        return catalog.fastTier(self.tool, self.model) is not None

    def getServiceTier(self) -> Optional[str]:
        """Fast モードがオンで使えるなら、その service tier の id。"""
        if not self.isFastAvailable() or self._fast_provider is None:
            return None
        try:
            enabled = bool(self._fast_provider())
        except Exception:
            errorLogging()
            return None
        return catalog.fastTier(self.tool, self.model) if enabled else None

    def restartSession(self) -> None:
        """エフォートを変えたときに呼ぶ。今のセッションを閉じ、次の翻訳か updateClient() で作り直す。"""
        with self._lock:
            old, self._session = self._session, None
        if old is not None:
            old.shutdown()

    def authenticationCheck(self) -> bool:
        return self.tool is not None and catalog.resolveExecutable(self.tool) is not None

    def setContextHistory(self, history_items: list[dict]) -> None:
        self._context_history = history_items or []

    # --- 状態の通知とサーキットブレーカー ---
    def setStatusCallback(self, callback: Optional[Callable[[bool], None]]) -> None:
        self._status_callback = callback

    def isAvailable(self) -> bool:
        with self._lock:
            return self._broken_reason is None

    def resetBreaker(self) -> None:
        """接続確認が通ったときに呼ぶ。通知はしない (接続確認の応答が UI に伝える)。"""
        with self._lock:
            self._broken_reason = None
            self._retry_at = 0.0

    def _notify(self, available: bool) -> None:
        callback = self._status_callback
        if callback is None:
            return
        try:
            callback(available)
        except Exception:
            errorLogging()

    def _trip(self, reason: str) -> None:
        with self._lock:
            if self._shut_down:
                return
            was_available = self._broken_reason is None
            self._broken_reason = reason or "AI CLI failed to start"
            self._retry_at = time.monotonic() + self.BREAKER_SECONDS
        if was_available:
            self._notify(False)

    def _markHealthy(self) -> None:
        with self._lock:
            was_broken = self._broken_reason is not None
            self._broken_reason = None
            self._retry_at = 0.0
        if was_broken:
            self._notify(True)

    def _raiseIfBroken(self) -> None:
        start_probe = False
        with self._lock:
            reason = self._broken_reason
            if reason is None:
                return
            if time.monotonic() >= self._retry_at and not self._probing:
                self._probing = True
                start_probe = True
        if start_probe:
            # 翻訳は待たせずに失敗させ、立ち直ったかどうかは裏で確かめる。
            self._startProbe()
        raise AiCliError(f"AI CLI is unavailable: {reason}")

    def _isCurrent(self, session) -> bool:
        with self._lock:
            return self._session is session and not self._shut_down

    # --- セッション ---
    def _ensureSession(self):
        with self._lock:
            if self._shut_down:
                raise AiCliError("AI CLI client was shut down")
            if self.tool is None or not self.model:
                raise AiCliError("AI CLI tool or model is not selected")
            if self._session is None:
                executable = catalog.resolveExecutable(self.tool)
                if executable is None:
                    raise AiCliError(f"{self.tool} is not installed", startup=True)
                self._session = SESSION_CLASSES[self.tool](
                    command_prefix=[executable], model=self.model,
                    workspace=self.workspace, base_instructions=BASE_INSTRUCTIONS,
                    client_version=self.client_version, effort=self.getEffort(),
                    service_tier=self.getServiceTier(),
                )
            return self._session

    def updateClient(self) -> None:
        """セッションを裏で起動し、確認のターンを 1 回送っておく。

        claude の起動 (数秒〜30 秒) を最初の翻訳で待たないためと、未ログインなどを
        最初のメッセージより前に見つけるため。既にターンを終えたセッションには送らない。
        """
        self._startProbe()

    def _startProbe(self) -> None:
        threading.Thread(target=self._probe, name="ai-cli-warmup", daemon=True).start()

    def _probe(self) -> None:
        session = None
        try:
            with self._lock:
                if self._shut_down or self.tool is None or not self.model:
                    return
            session = self._ensureSession()
            if session.isAlive() and session.turnCount() > 0:
                return
            session.translate(PROBE_PROMPT)
            if self._isCurrent(session):
                self._markHealthy()
        except AiCliToolUseError:
            errorLogging()
        except AiCliError as e:
            # CLI やモデルを切り替えた・終了したために古いセッションが止められた場合は、
            # CLI の故障ではないので何もしない。
            if session is None or self._isCurrent(session):
                errorLogging()
                self._trip(str(e))
        except Exception:
            errorLogging()
        finally:
            with self._lock:
                self._probing = False

    def translate(self, text: str, input_lang: str, output_lang: str) -> str:
        self._raiseIfBroken()
        instructions = buildSystemPrompt(
            self.prompt_template, self.supported_languages, input_lang, output_lang,
            self.history_cfg, self._context_history,
        )
        prompt = f"{instructions.rstrip()}\n\nText to translate:\n{text}"
        session = self._ensureSession()
        try:
            result = session.translate(prompt)
        except AiCliToolUseError:
            # 入力に紛れ込んだ指示によるもの。CLI は壊れていないのでブレーカーは開かない。
            raise
        except AiCliError as e:
            if e.startup and self._isCurrent(session):
                self._trip(str(e))
            raise
        self._markHealthy()
        if not result.strip() and text.strip():
            raise AiCliError("AI CLI returned an empty translation")
        return result

    def close(self) -> None:
        """今のセッションを終える (再起動可能): setTool()/setModel() から使う。

        session.close() ではなく session.shutdown() (恒久停止) を呼ぶ。
        session.close() (再起動可能な kill) だと、既にこのセッションを掴んでいる
        warm-up スレッドが後から respawn してしまい、孤児プロセスが残る可能性が
        あるため。AICliClient 自体はまだ生きているので、次の translate()/
        updateClient() は新しいセッションを普通に作り直す。
        """
        with self._lock:
            session = self._session
            self._session = None
        if session is not None:
            session.shutdown()

    def shutdown(self) -> None:
        """クライアント自体を恒久的に終える。以後 translate()/updateClient() は何もしない。"""
        with self._lock:
            self._shut_down = True
            session, self._session = self._session, None
        if session is not None:
            session.shutdown()

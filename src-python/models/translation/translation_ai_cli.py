"""翻訳エンジン「AI CLI」: ユーザーの PC にある codex / claude / agy を常駐させて翻訳する。

CLI ごとに常駐セッションを 1 本持つ。CLI かモデルを変えたらセッションを閉じ、
次の翻訳か updateClient() (裏で起動しておく) で作り直す。入力・出力言語は
翻訳ごとに変わるので、指示は毎回のプロンプトに含める。

close() は session.shutdown() (恒久停止) を呼ぶ。session.close() (再起動可能な kill)
だと、既に古いセッションを掴んでいる warm-up スレッドがそれを生き返らせてしまい、
孤児プロセスが残る可能性があるため。
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
    from utils import errorLogging
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
    from utils import errorLogging

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
        self._shut_down = False
        self._lock = threading.Lock()

    def getInstalledTools(self) -> list[str]:
        return catalog.detectInstalledTools()

    def getTool(self) -> Optional[str]:
        return self.tool

    def setTool(self, tool: str) -> bool:
        if tool not in self.getInstalledTools():
            return False
        if tool != self.tool:
            with self._lock:
                self.tool = tool
                self.model = None
                old, self._session = self._session, None
            if old is not None:
                old.shutdown()
        return True

    def getModelList(self) -> list[str]:
        return catalog.listModels(self.tool) if self.tool else []

    def getModel(self) -> Optional[str]:
        return self.model

    def setModel(self, model: str) -> bool:
        if model not in self.getModelList():
            return False
        if model != self.model:
            with self._lock:
                self.model = model
                old, self._session = self._session, None
            if old is not None:
                old.shutdown()
        return True

    def authenticationCheck(self) -> bool:
        return self.tool is not None and catalog.resolveExecutable(self.tool) is not None

    def setContextHistory(self, history_items: list[dict]) -> None:
        self._context_history = history_items or []

    def _ensureSession(self):
        with self._lock:
            if self._shut_down:
                raise AiCliError("AI CLI client was shut down")
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
                # 起動に失敗しても、次の翻訳でもう一度試してエラーを返す。
                # shutdown 後もここに来る (_ensureSession が AiCliError を送出する) が、
                # その場合は何もしない。
                errorLogging()
        threading.Thread(target=warmUp, name="ai-cli-warmup", daemon=True).start()

    def translate(self, text: str, input_lang: str, output_lang: str) -> str:
        instructions = buildSystemPrompt(
            self.prompt_template, self.supported_languages, input_lang, output_lang,
            self.history_cfg, self._context_history,
        )
        prompt = f"{instructions.rstrip()}\n\nText to translate:\n{text}"
        return self._ensureSession().translate(prompt)

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

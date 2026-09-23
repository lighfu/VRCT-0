"""OpenAI Codex CLI (`codex app-server`) を常駐させるアダプター。

app-server は 1 行 1 メッセージの JSON-RPC。initialize → initialized 通知 →
config/read → thread/start で 1 本のスレッドを作り、翻訳ごとに turn/start を送る。
応答は item/completed (item.type == "agentMessage") の text で、
turn/completed が来たら完了。読み取り専用・承認なしで動かす。

**ツールを使わせない仕組み** (codex 0.155.1 の実機で確認、2026-09-24):
app-server はユーザーの `~/.codex/config.toml` を読む (MCP サーバー・notify・
プラグイン・メモリなど)。ログイン情報も同じ場所にあるので CODEX_HOME は替えられない。
そこで次を重ねる。

1. `-c` の上書き (`_OVERRIDES`): 通知プログラム (notify)・Web 検索・シェル・
   アプリ/プラグイン/ブラウザー/コンピューター操作・画像・サブエージェント・
   フック・メモリなどの機能を切る。
2. MCP サーバーは `-c mcp_servers={}` では消えない (設定が重ね合わされる) ので、
   config/read で実際の MCP サーバー名を読み、thread/start の `config` で
   1 つずつ `enabled = false` にする。実機で MCP サーバーの子プロセスが
   起動しなくなることを確認した。
3. thread/start の `ephemeral: true`: スレッドを保存しない (ユーザーの
   `~/.codex/sessions` にもスレッドの履歴 DB にも翻訳した発話が残らない)。

外せないもの (設計書に記載し、UI の説明文で知らせる):
- ユーザーの `~/.codex/AGENTS.md` (`project_doc_max_bytes=0` で外れるのは作業フォルダ側の
  AGENTS.md だけ)。翻訳の指示は毎ターンのプロンプトで明示しているが、混ざる可能性は残る。
- codex のデバッグ用ログ (`~/.codex/logs_2.sqlite`) には、送ったターンの本文が
  DEBUG で記録される (codex が一定期間で消す)。`-c sqlite_home=...` で VRCT の
  フォルダに移せるが、初回の起動が 44 秒かかり、ユーザーのスレッドの一覧
  (題名・最初のメッセージ) を移した先にコピーするので採らない (2026-09-24 実測)。

実行時の見張り: userMessage / agentMessage / reasoning / contextCompaction 以外の
item (commandExecution・mcpToolCall・webSearch・fileChange など) や、
サーバーからの要求 (承認要求など) を見たら、そのターンを ("tool", ...) で失敗させる。
"""

try:
    from .base import AiCliError, CliSession
except ImportError:
    from base import AiCliError, CliSession

_OVERRIDES = [
    "notify=[]",
    'web_search="disabled"',
    'history.persistence="none"',
    "project_doc_max_bytes=0",
    "check_for_update_on_startup=false",
    "include_apps_instructions=false",
    "include_permissions_instructions=false",
    "include_collaboration_mode_instructions=false",
    "include_environment_context=false",
] + [f"features.{name}=false" for name in (
    "shell_tool", "unified_exec", "shell_snapshot", "apps", "plugins", "remote_plugin",
    "browser_use", "browser_use_external", "in_app_browser", "computer_use",
    "image_generation", "view_image", "multi_agent", "goals", "hooks", "memories",
    "tool_suggest", "skill_search", "sleep_tool", "workspace_dependencies",
)]

# ツールを使わない普通のターンで出る item。contextCompaction は長い会話で codex が
# 自動で要約したときのもので、ツールではない。
_ALLOWED_ITEM_TYPES = {"userMessage", "agentMessage", "reasoning", "contextCompaction"}


class CodexSession(CliSession):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._rpc_id = 0
        self._thread_id = None
        self._turn_request_id = None
        self._messages_in_turn: list[dict] = []

    def _buildArgs(self) -> list[str]:
        args = []
        for override in _OVERRIDES:
            args += ["-c", override]
        return args + ["app-server"]

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
        self._request("initialize", {"clientInfo": {"name": "vrct", "version": self.client_version or "0"}}, deadline)
        self._write({"method": "initialized"})
        # ユーザー設定の MCP サーバー名を読み、このスレッドでは全部止める。
        config = self._request("config/read", {"cwd": self.workspace, "includeLayers": False}, deadline)
        config = config.get("config", config) if isinstance(config, dict) else {}
        servers = config.get("mcp_servers") if isinstance(config, dict) else None
        thread_config = {}
        if isinstance(servers, dict) and servers:
            thread_config["mcp_servers"] = {str(name): {"enabled": False} for name in servers}
        params = {
            "model": self.model,
            "approvalPolicy": "never",
            "sandbox": "read-only",
            "baseInstructions": self.base_instructions,
            "ephemeral": True,
        }
        if thread_config:
            params["config"] = thread_config
        result = self._request("thread/start", params, deadline)
        thread_id = (result.get("thread") or {}).get("id")
        if not thread_id:
            raise AiCliError("codex thread/start returned no thread id")
        self._thread_id = thread_id

    def _turnMessages(self, prompt: str) -> list[dict]:
        self._messages_in_turn = []
        self._turn_request_id = self._newId()
        return [{"method": "turn/start", "id": self._turn_request_id,
                 "params": {"threadId": self._thread_id, "input": [{"type": "text", "text": prompt}],
                            "effort": "low"}}]

    def _finalText(self) -> str:
        """最終回答だけを返す。途中経過 (phase: commentary) は訳文に混ぜない。"""
        finals = [m for m in self._messages_in_turn if m.get("phase") == "final_answer"]
        if finals:
            return str(finals[-1].get("text") or "")
        others = [m for m in self._messages_in_turn if m.get("phase") != "commentary"]
        if others:
            return str(others[-1].get("text") or "")
        return ""

    def _interpret(self, message: dict):
        if message.get("id") == self._turn_request_id and "error" in message:
            return ("error", f"codex turn/start failed: {message['error']}")
        method = message.get("method")
        params = message.get("params") or {}
        if method and "id" in message:
            # サーバーからの要求 (コマンド実行の承認・MCP の問い合わせなど)。翻訳では起きないはず。
            return ("tool", f"codex asked for {method}; the turn was refused")
        if method in ("item/started", "item/completed"):
            item = params.get("item") or {}
            item_type = item.get("type")
            if item_type not in _ALLOWED_ITEM_TYPES:
                return ("tool", f"codex tried to use a tool ({item_type}); the turn was refused")
            if method == "item/completed" and item_type == "agentMessage":
                self._messages_in_turn.append(item)
            return None
        if method == "turn/completed":
            turn = params.get("turn") or {}
            if turn.get("status") in ("failed", "interrupted"):
                return ("error", f"codex turn {turn.get('status')}: {turn.get('error')}")
            return ("ok", self._finalText())
        if method == "error":
            if params.get("willRetry"):
                # 一時的なストリームの切断など。codex 自身が再試行するので待ち続ける。
                return None
            return ("error", f"codex error: {params}")
        return None

"""Antigravity CLI (`agy`) を stream-json で常駐させるアダプター。

`-p` を先頭に置くと次のフラグをプロンプトとして読んでしまうので、空の値を
付けた `--print=` を最後に置く。入力は `{"event":"user","message":{...}}`。

**ツールを使わせない仕組み** (agy 1.2.9 の実機で確認、2026-09-24):
agy にはツールを止めるフラグが無い (`--mode plan` も `--sandbox` も、作業フォルダの
外のファイルを読む view_file を止めなかった)。そこで次の 3 つを重ねる。

1. **専用のホーム**: `USERPROFILE`/`HOME` を VRCT 専用のフォルダに向けて起動する。
   agy の設定・会話の保存先 (`~/.gemini/antigravity-cli`, `~/.gemini/config`) が
   そこに移るので、ユーザーの MCP サーバー・許可ルール・GEMINI.md を読まず、
   翻訳した発話がユーザーの agy の履歴に残らない。ログイン情報は OS 側に
   保存されているので、ホームを替えてもログインしたまま動く。
2. **ツールの無いエージェント**: 専用ホームの `~/.gemini/config/agents/` に
   `tools: []`・`excludeDefaultComponents: true`・`inheritMcp: false` の
   エージェントを置き、`--agent` で選ぶ。実機ではモデルが使えるツールが
   `manage_task` (自分のバックグラウンド作業の一覧・停止) だけになった。
   init イベントの tools は既定の一覧のまま出るので、確認には使えない。
3. **拒否ルール**: 専用ホームの settings.json に `toolPermission: "strict"` と
   全種類の deny ルールを書く (エージェントの指定が効かなかったときの保険)。

さらに実行時の見張りとして、ツールの step_update や denied_actions の増加を
見たらそのターンを `("tool", ...)` で失敗させる (基底がプロセスを殺す)。

会話はプロセスごとに 1 本で、専用ホームの下に会話 ID の名前で保存される。
プロセスを止めたら、その会話 ID のファイルだけを消す。
"""

import json
import os
import shutil
import threading
import time

try:
    from .base import CliSession
except ImportError:
    from base import CliSession

AGENT_NAME = "vrct-translator"
_DENY_RULES = ["command(*)", "read_file(*)", "write_file(*)", "read_url(*)", "execute_url(*)", "mcp(*)", "unsandboxed(*)"]
_AGENT_TEMPLATE = """---
name: {name}
description: Translation engine for VRCT. Translates the given text and has no tools.
mainAgent: true
subagent: false
hidden: true
tools: []
inheritMcp: false
inheritCustomizations: false
excludeDefaultComponents: true
---
# System Prompt
{instructions}
"""


def defaultHomeFor(workspace: str) -> str:
    """作業フォルダと同じ場所に置く、agy 専用のホーム。"""
    return os.path.join(os.path.dirname(os.path.abspath(workspace)), "ai_cli_agy_home")


def _writeText(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == text:
                return
    except OSError:
        pass
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


class AgySession(CliSession):
    def __init__(self, *args, home: str = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.home = home or defaultHomeFor(self.workspace)
        self._convByProc: dict = {}
        self._deniedSeen = 0

    # --- 起動 ---
    def _buildArgs(self) -> list[str]:
        return ["--input-format", "stream-json", "--output-format", "stream-json",
                "--model", self.model, "--agent", AGENT_NAME, "--disable-slash-commands", "--print="]

    def _buildEnv(self) -> dict:
        env = dict(os.environ)
        env["USERPROFILE"] = self.home
        env["HOME"] = self.home
        return env

    def _beforeSpawn(self) -> None:
        settings = {
            "toolPermission": "strict",
            "allowNonWorkspaceAccess": False,
            "permissions": {"allow": [], "deny": list(_DENY_RULES)},
        }
        _writeText(os.path.join(self.home, ".gemini", "antigravity-cli", "settings.json"),
                   json.dumps(settings, indent=2) + "\n")
        _writeText(os.path.join(self.home, ".gemini", "config", "agents", AGENT_NAME + ".md"),
                   _AGENT_TEMPLATE.format(name=AGENT_NAME, instructions=self.base_instructions))
        self._pruneLogs()

    def _pruneLogs(self, keep: int = 5) -> None:
        """agy は起動のたびに専用ホームの log/ にログを 1 つ書く。新しいものだけ残す。"""
        log_dir = os.path.join(self.home, ".gemini", "antigravity-cli", "log")
        try:
            logs = [os.path.join(log_dir, name) for name in os.listdir(log_dir) if name.endswith(".log")]
        except OSError:
            return
        logs.sort(key=lambda path: os.path.getmtime(path) if os.path.exists(path) else 0, reverse=True)
        for path in logs[keep:]:
            try:
                os.remove(path)
            except OSError:
                pass

    def _afterStart(self, deadline: float) -> None:
        # denied_actions はプロセスの中で累積するので、増えたかどうかをプロセスごとに数える。
        self._deniedSeen = 0

    # --- 1 ターン ---
    def _turnMessages(self, prompt: str) -> list[dict]:
        return [{"event": "user", "message": {"role": "user", "content": prompt}}]

    def _interpret(self, message: dict):
        event = message.get("event")
        if event == "init":
            conversation_id = message.get("conversation_id")
            if conversation_id and self._turnProc is not None:
                self._convByProc.setdefault(self._turnProc, set()).add(str(conversation_id))
            return None
        if event == "step_update":
            step = message.get("step_update") or {}
            if step.get("step_type") == "tool" or step.get("tool_info") or step.get("subagent_info"):
                tool = step.get("tool_name") or (step.get("tool_info") or {}).get("name") or "subagent"
                return ("tool", f"agy tried to use a tool ({tool}); the turn was refused")
            return None
        if event != "result":
            return None
        result = message.get("result") or {}
        denied = result.get("denied_actions") or []
        if len(denied) > self._deniedSeen:
            self._deniedSeen = len(denied)
            return ("tool", f"agy tried to use a tool that was denied ({denied[-1]}); the turn was refused")
        if result.get("status") == "SUCCESS":
            return ("ok", str(result.get("response", "")))
        return ("error", str(result.get("error") or result.get("status") or "agy error"))

    # --- 後始末 ---
    def _onProcessGone(self, proc) -> None:
        conversation_ids = self._convByProc.pop(proc, set())
        if conversation_ids:
            threading.Thread(target=self._removeConversations, args=(sorted(conversation_ids),),
                             name="ai-cli-agy-cleanup", daemon=True).start()

    def _removeConversations(self, conversation_ids: list[str]) -> None:
        """自分の会話 (ID で特定) の保存物だけを専用ホームから消す。

        プロセスを殺した直後はファイルがまだ掴まれていることがあるので、少し待って数回試す。
        """
        store = os.path.join(self.home, ".gemini", "antigravity-cli")
        paths = []
        for cid in conversation_ids:
            # 会話 ID は UUID。パスとして解釈できる文字が入っていたら触らない。
            if not cid or any(ch in cid for ch in "/\\.:"):
                continue
            paths += [
                os.path.join(store, "conversations", cid + ".db"),
                os.path.join(store, "conversations", cid + ".db-wal"),
                os.path.join(store, "conversations", cid + ".db-shm"),
                os.path.join(store, "brain", cid),
                os.path.join(store, "annotations", cid + ".pbtxt"),
                os.path.join(store, "presence", cid + ".lock"),
            ]
        for _attempt in range(10):
            remaining = []
            for path in paths:
                try:
                    if os.path.isdir(path):
                        shutil.rmtree(path)
                    elif os.path.exists(path):
                        os.remove(path)
                except OSError:
                    remaining.append(path)
            if not remaining:
                return
            paths = remaining
            time.sleep(0.5)

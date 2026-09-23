"""Claude Code (`claude`) を stream-json で常駐させるアダプター。

ユーザー設定 (フック・プラグイン・MCP) を読むと 1 ターン 17 秒以上かかる
(2026-09-24 実測) ので `--setting-sources project --strict-mcp-config` で
読ませない。`--bare` はログイン情報も読まなくなるので使わない。

ツールは `--tools ""` で空にする。claude 2.1.281 の実機では、この起動引数で
system/init の tools と mcp_servers が両方とも空になった (claude.ai の
コネクターも出ない)。実行時の見張りとして、system/init にツールか MCP サーバーが
1 つでも出たら起動の失敗 (設定が効いていない) として扱い、assistant の
tool_use ブロックを見たらそのターンを ("tool", ...) で失敗させる。
"""

try:
    from .base import CliSession
except ImportError:
    from base import CliSession

_TOOL_BLOCK_TYPES = {"tool_use", "server_tool_use", "mcp_tool_use"}


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
        kind = message.get("type")
        if kind == "system" and message.get("subtype") == "init":
            tools = message.get("tools") or []
            servers = message.get("mcp_servers") or []
            if tools or servers:
                # 起動引数が効いていない。ツールの試行ではなく起動の失敗として扱う。
                return ("error", f"claude started with tools {tools} and MCP servers {servers}; refusing to use it")
            return None
        if kind == "assistant":
            for block in (message.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") in _TOOL_BLOCK_TYPES:
                    return ("tool", f"claude tried to use a tool ({block.get('name')}); the turn was refused")
            return None
        if kind != "result":
            return None
        if message.get("is_error"):
            return ("error", str(message.get("result") or message.get("subtype") or "claude error"))
        return ("ok", str(message.get("result", "")))

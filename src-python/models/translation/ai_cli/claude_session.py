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

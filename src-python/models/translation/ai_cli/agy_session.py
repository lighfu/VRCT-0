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

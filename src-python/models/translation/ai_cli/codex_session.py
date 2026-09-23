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
                 "params": {"threadId": self._thread_id, "input": [{"type": "text", "text": prompt}],
                            "effort": "low"}}]

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

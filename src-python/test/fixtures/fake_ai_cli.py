"""AI CLI (claude / agy / codex) の常駐プロトコルをまねる偽 CLI。テスト専用。

翻訳要求には "T(<プロンプトの最終行>)" を返す。オプションで固まる・落ちる・
エラーを返す・ツールを使おうとする・空の応答を返す、を再現する。
ツール関連のイベントの形は、実機 (agy 1.2.9 / codex 0.155.1 / claude 2.1.281、
2026-09-24) で記録したものに合わせてある。
"""

import argparse
import json
import os
import sys
import time
import uuid


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=["claude", "agy", "codex"])
    parser.add_argument("--hang-on")
    parser.add_argument("--exit-on")
    parser.add_argument("--error-on")
    parser.add_argument("--record")
    parser.add_argument("--no-read", action="store_true")
    # ツールを使おうとする (agy: step_update tool / codex: commandExecution / claude: tool_use)。
    parser.add_argument("--tool-on")
    # agy: 実機と同じく、ツールが自動で拒否されて response が空の SUCCESS になる。
    parser.add_argument("--deny-on")
    # agy: step_update は出さずに denied_actions だけが増える。
    parser.add_argument("--deny-only-on")
    parser.add_argument("--empty-on")
    # claude: system/init が報告するツールと MCP サーバー (カンマ区切り)。
    parser.add_argument("--init-tools", default="")
    parser.add_argument("--init-mcp", default="")
    # claude: 未ログインのときの応答 (is_error の result)。
    parser.add_argument("--logged-out", action="store_true")
    # codex: 一時的なエラー (willRetry: true) を挟んでから普通に答える。
    parser.add_argument("--retry-error-on")
    # codex: 途中経過のメッセージ (phase: commentary) を最終回答の前に出す。
    parser.add_argument("--commentary", action="store_true")
    # codex: サーバーからクライアントへの要求 (承認要求など) を出す。
    parser.add_argument("--request-on")
    # codex: config/read が返す MCP サーバー名 (カンマ区切り)。
    parser.add_argument("--mcp-servers", default="")
    # agy: USERPROFILE の下に会話の保存物 (会話 ID 名) を作る。
    parser.add_argument("--write-artifacts", action="store_true")
    args, _unknown = parser.parse_known_args()

    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")

    def record(obj):
        if args.record:
            with open(args.record, "a", encoding="utf-8") as f:
                f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def emit(obj):
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    def on(flag, prompt):
        return bool(flag and flag in prompt)

    record({"argv": sys.argv[1:], "env": {k: os.environ.get(k) for k in ("USERPROFILE", "HOME")}})
    if args.no_read:
        # stdin を一切読まない (書き込み側がブロックし続けるケースの再現用)。
        time.sleep(3600)
        return

    conversation_id = str(uuid.uuid4())
    denied_actions = []
    if args.mode == "agy" and args.write_artifacts:
        base = os.path.join(os.environ.get("USERPROFILE", "."), ".gemini", "antigravity-cli")
        os.makedirs(os.path.join(base, "conversations"), exist_ok=True)
        os.makedirs(os.path.join(base, "brain", conversation_id, ".system_generated"), exist_ok=True)
        os.makedirs(os.path.join(base, "annotations"), exist_ok=True)
        for path in (os.path.join(base, "conversations", conversation_id + ".db"),
                     os.path.join(base, "brain", conversation_id, ".system_generated", "transcript.jsonl"),
                     os.path.join(base, "annotations", conversation_id + ".pbtxt")):
            with open(path, "w", encoding="utf-8") as f:
                f.write("x")
        record({"conversation_id": conversation_id})

    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        message = json.loads(raw)
        record({"in": message})
        if args.mode == "codex":
            method = message.get("method")
            if method == "initialize":
                emit({"id": message["id"], "result": {"userAgent": "fake"}})
                continue
            if method == "config/read":
                servers = {name: {"command": name, "enabled": True} for name in args.mcp_servers.split(",") if name}
                emit({"id": message["id"], "result": {"config": {"mcp_servers": servers, "notify": []}}})
                continue
            if method == "thread/start":
                emit({"id": message["id"], "result": {"thread": {"id": "th-1", "ephemeral": message["params"].get("ephemeral")}}})
                continue
            if method != "turn/start":
                continue
            prompt = message["params"]["input"][0]["text"]
        elif args.mode == "claude":
            prompt = message["message"]["content"]
        else:
            prompt = message["message"]["content"]

        if on(args.hang_on, prompt):
            time.sleep(3600)
        if on(args.exit_on, prompt):
            sys.exit(3)
        answer = "T(" + prompt.strip().splitlines()[-1] + ")"
        if on(args.empty_on, prompt):
            answer = ""
        failed = on(args.error_on, prompt)

        if args.mode == "claude":
            tools = [t for t in args.init_tools.split(",") if t]
            mcp = [{"name": n, "status": "connected"} for n in args.init_mcp.split(",") if n]
            emit({"type": "system", "subtype": "init", "tools": tools, "mcp_servers": mcp, "model": "fake"})
            if args.logged_out:
                emit({"type": "result", "subtype": "success", "is_error": True, "result": "Not logged in · Please run /login"})
                continue
            if on(args.tool_on, prompt):
                emit({"type": "assistant", "message": {"content": [
                    {"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {"file_path": "C:/secret.txt"}}]}})
            emit({"type": "assistant", "message": {"content": [{"type": "text", "text": answer}]}})
            emit({"type": "result", "subtype": "error" if failed else "success",
                  "is_error": failed, "result": "boom" if failed else answer})
        elif args.mode == "agy":
            emit({"event": "init", "conversation_id": conversation_id,
                  "init": {"model": "fake", "tools": ["view_file", "run_command"], "permission_mode": "strict"}})
            emit({"event": "step_update", "step_update": {"conversation_id": conversation_id, "state": "DONE", "step_type": "user_input"}})
            response = answer
            if on(args.tool_on, prompt):
                emit({"event": "step_update", "step_update": {"state": "ACTIVE", "step_type": "tool", "tool_name": "view_file",
                                                              "tool_info": {"name": "view_file", "parameters": {"AbsolutePath": "C:\\secret.txt"}}}})
                emit({"event": "step_update", "step_update": {"state": "DONE", "step_type": "tool", "tool_name": "view_file",
                                                              "tool_info": {"name": "view_file", "output": "1 lines"}}})
                response = "The file says: secret"
            if on(args.deny_on, prompt):
                emit({"event": "step_update", "step_update": {"state": "ACTIVE", "step_type": "tool", "tool_name": "run_command",
                                                              "tool_info": {"name": "run_command", "parameters": {"CommandLine": "dir"}}}})
                emit({"event": "step_update", "step_update": {"state": "ERROR", "step_type": "tool", "tool_name": "run_command",
                                                              "tool_info": {"name": "run_command", "error": {"type": "TOOL_ERROR"}}}})
                denied_actions.append({"action": "command", "display_name": "RunCommand"})
                response = ""
            if on(args.deny_only_on, prompt):
                denied_actions.append({"action": "read_url", "display_name": "ReadUrlContent"})
                response = ""
            if response:
                emit({"event": "step_update", "step_update": {"state": "ACTIVE", "step_type": "agent_response", "text_delta": response}})
            result = {"conversation_id": conversation_id, "status": "ERROR" if failed else "SUCCESS",
                      "response": "" if failed else (response + "\n" if response else "")}
            if failed:
                result["error"] = "boom"
            if denied_actions:
                # 実機と同じく、プロセス内で累積したものが毎回入る。
                result["denied_actions"] = list(denied_actions)
            emit({"event": "result", "result": result})
        else:
            emit({"id": message["id"], "result": {"turn": {"id": "turn-1"}}})
            emit({"method": "item/started", "params": {"item": {"type": "userMessage", "id": "u1"}}})
            if on(args.retry_error_on, prompt):
                emit({"method": "error", "params": {"error": {"message": "stream disconnected"}, "willRetry": True}})
            if on(args.request_on, prompt):
                emit({"id": 99, "method": "item/commandExecution/requestApproval", "params": {"command": "dir"}})
            if on(args.tool_on, prompt):
                emit({"method": "item/started", "params": {"item": {"type": "commandExecution", "id": "c1", "command": "type secret.txt"}}})
                emit({"method": "item/completed", "params": {"item": {"type": "commandExecution", "id": "c1", "command": "type secret.txt",
                                                                       "aggregatedOutput": "secret"}}})
            if failed:
                emit({"method": "turn/completed", "params": {"turn": {"status": "failed", "error": {"message": "boom"}}}})
                continue
            if args.commentary:
                emit({"method": "item/completed", "params": {"item": {"type": "agentMessage", "phase": "commentary", "text": "Translating now."}}})
            emit({"method": "item/completed", "params": {"item": {"type": "reasoning", "id": "r1"}}})
            emit({"method": "item/completed", "params": {"item": {"type": "agentMessage", "phase": "final_answer", "text": answer}}})
            emit({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})


if __name__ == "__main__":
    main()

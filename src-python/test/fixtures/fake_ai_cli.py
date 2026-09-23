"""AI CLI (claude / agy / codex) の常駐プロトコルをまねる偽 CLI。テスト専用。

翻訳要求には "T(<プロンプトの最終行>)" を返す。オプションで固まる・落ちる・
エラーを返す・(agy の) ツール許可を求める、を再現する。
"""

import argparse
import json
import sys
import time


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=["claude", "agy", "codex"])
    parser.add_argument("--hang-on")
    parser.add_argument("--exit-on")
    parser.add_argument("--error-on")
    parser.add_argument("--permission-on")
    parser.add_argument("--record")
    args, _unknown = parser.parse_known_args()

    def record(obj):
        if args.record:
            with open(args.record, "a", encoding="utf-8") as f:
                f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def emit(obj):
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    record({"argv": sys.argv[1:]})
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
            if method == "thread/start":
                emit({"id": message["id"], "result": {"thread": {"id": "th-1"}}})
                continue
            if method != "turn/start":
                continue
            prompt = message["params"]["input"][0]["text"]
        elif args.mode == "claude":
            prompt = message["message"]["content"]
        else:
            prompt = message["message"]["content"]

        if args.hang_on and args.hang_on in prompt:
            time.sleep(3600)
        if args.exit_on and args.exit_on in prompt:
            sys.exit(3)
        answer = "T(" + prompt.strip().splitlines()[-1] + ")"
        failed = bool(args.error_on and args.error_on in prompt)

        if args.mode == "claude":
            emit({"type": "assistant", "message": {"content": [{"type": "text", "text": answer}]}})
            emit({"type": "result", "subtype": "error" if failed else "success",
                  "is_error": failed, "result": "boom" if failed else answer})
        elif args.mode == "agy":
            if args.permission_on and args.permission_on in prompt:
                emit({"event": "ask_permission", "ask_permission": {"tool": "browser_click_element"}})
                continue
            emit({"event": "step_update", "step_update": {"text_delta": answer}})
            if failed:
                emit({"event": "result", "result": {"status": "ERROR", "response": "", "error": "boom"}})
            else:
                emit({"event": "result", "result": {"status": "SUCCESS", "response": answer + "\n"}})
        else:
            emit({"id": message["id"], "result": {"turn": {"id": "turn-1"}}})
            if failed:
                emit({"method": "turn/completed", "params": {"turn": {"status": "failed", "error": {"message": "boom"}}}})
            else:
                emit({"method": "item/completed", "params": {"item": {"type": "agentMessage", "text": answer}}})
                emit({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})


if __name__ == "__main__":
    main()

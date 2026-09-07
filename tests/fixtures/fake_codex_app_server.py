"""Small JSONL process used to exercise the Codex app-server boundary."""

import json
import sys

MODE = sys.argv[1] if len(sys.argv) > 1 else "normal"


def send(message: dict[str, object]) -> None:
    print(json.dumps(message), flush=True)


for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    request_id = message.get("id")

    if method == "initialize":
        if MODE == "malformed":
            print("not-json", flush=True)
            break
        if MODE == "premature-eof":
            break
        send(
            {
                "id": request_id,
                "result": {
                    "userAgent": "fake-codex/1.0",
                    "platformFamily": "unix",
                    "platformOs": "test",
                },
            }
        )
    elif method == "initialized":
        continue
    elif method == "account/read":
        if MODE == "request-error":
            send({"id": request_id, "error": {"code": -32000, "message": "denied"}})
            continue
        if MODE == "invalid-envelope":
            send({"id": str(request_id), "result": {}})
            continue
        if MODE == "noisy-stderr":
            print("x" * 1_000_000, file=sys.stderr, flush=True)
        send(
            {
                "id": request_id,
                "result": {"account": None, "requiresOpenaiAuth": True},
            }
        )
    elif method == "account/login/start":
        send(
            {
                "id": request_id,
                "result": {
                    "type": "chatgptDeviceCode",
                    "loginId": "login-1",
                    "verificationUrl": "https://auth.openai.com/codex/device",
                    "userCode": "ABCD-1234",
                },
            }
        )
        send(
            {
                "method": "account/login/completed",
                "params": {"loginId": "login-1", "success": True, "error": None},
            }
        )
    elif method == "thread/start":
        send({"method": "thread/started", "params": {"thread": {"id": "thread-1"}}})
        send(
            {
                "id": request_id,
                "result": {"thread": {"id": "thread-1", "ephemeral": False}},
            }
        )
    elif method == "thread/resume":
        thread_id = message["params"]["threadId"]
        send({"id": request_id, "result": {"thread": {"id": thread_id}}})
    elif method == "turn/start":
        thread_id = message["params"]["threadId"]
        send(
            {
                "id": request_id,
                "result": {"turn": {"id": "turn-1", "status": "inProgress", "items": []}},
            }
        )
        if MODE == "normal":
            send(
                {
                    "method": "item/agentMessage/delta",
                    "params": {"threadId": thread_id, "turnId": "turn-1", "delta": "hello"},
                }
            )
        terminal_status = {
            "turn-failed": "failed",
            "turn-interrupted": "interrupted",
        }.get(MODE, "completed")
        terminal_turn = (
            {"id": "turn-1"}
            if MODE == "malformed-terminal"
            else {"id": "turn-1", "status": terminal_status, "error": None}
        )
        send(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": thread_id,
                    "turn": terminal_turn,
                },
            }
        )
    elif method == "turn/interrupt":
        send({"id": request_id, "result": {}})

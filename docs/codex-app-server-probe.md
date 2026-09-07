# Codex app-server probe

This standalone probe checks the Codex app-server connection without changing the existing
LangGraph assistant runtime. It uses Codex-managed ChatGPT authentication; no ChatGPT token or
API key is accepted on the command line or stored in this repository.

## Prerequisites

- Install a current Codex CLI that provides `codex app-server`.
- Have a ChatGPT plan with Codex access.

## Start a new thread

```sh
uv run python codex_probe.py "Reply with a short connection confirmation"
```

If Codex is not authenticated, the probe prints the official device-login URL and one-time code.
Complete that flow in a browser. Codex owns and refreshes the resulting local credentials.

The final `Codex thread: ...` line is the persisted thread identifier. Save it if you want to
continue the same conversation.

## Resume a thread

```sh
uv run python codex_probe.py \
  --thread-id THREAD_ID \
  "Continue the previous conversation"
```

Use `--cwd PATH` when a new thread should be rooted in a directory other than the current one.
The probe streams agent-message text and exits nonzero when app-server reports a failed or
interrupted terminal status, emits invalid JSON, returns a protocol error, or exits prematurely.

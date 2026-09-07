"""Manual end-to-end probe for a local Codex app-server installation."""

import argparse
import asyncio
import sys
from pathlib import Path

from codex_app_server import CodexAppServerClient, CodexAppServerError


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", help="text prompt to send to Codex")
    parser.add_argument("--thread-id", help="resume this persisted Codex thread")
    parser.add_argument("--cwd", default=str(Path.cwd()), help="working directory for a new thread")
    parser.add_argument(
        "--server-command",
        nargs=argparse.REMAINDER,
        default=["codex", "app-server"],
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()
    if not args.server_command:
        parser.error("--server-command requires a command")
    return args


async def run_probe(args: argparse.Namespace) -> None:
    """Authenticate if needed, run one Codex turn, and print its result."""
    async with CodexAppServerClient(command=args.server_command) as client:
        account = await client.read_account()
        if account.get("requiresOpenaiAuth") is True and account.get("account") is None:
            login = await client.start_device_code_login()
            print(f"Open {login.verification_url}")
            print(f"Enter code: {login.user_code}")
            await client.wait_for_login(login.login_id)
            print("ChatGPT login completed")

        thread_id = (
            await client.resume_thread(args.thread_id)
            if args.thread_id
            else await client.start_thread(cwd=args.cwd)
        )
        turn = await client.start_turn(thread_id, args.prompt)
        terminal_status: str | None = None
        async for event in client.stream_turn(turn):
            if event.text_delta:
                print(event.text_delta, end="", flush=True)
            if event.terminal_status:
                terminal_status = event.terminal_status
        print()
        if terminal_status != "completed":
            raise CodexAppServerError(
                f"Codex turn ended with status {terminal_status or 'unknown'}"
            )
        print(f"Codex thread: {thread_id}")


def main() -> int:
    args = parse_args()
    try:
        asyncio.run(run_probe(args))
    except (CodexAppServerError, OSError) as exc:
        print(f"Codex probe failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

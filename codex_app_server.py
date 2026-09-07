"""Async stdio client for the Codex app-server protocol."""

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from contextlib import suppress
from dataclasses import dataclass


class CodexAppServerError(RuntimeError):
    """Raised when the Codex app-server process or protocol fails."""


@dataclass(frozen=True)
class DeviceCodeLogin:
    """Instructions for completing a ChatGPT device-code login."""

    login_id: str
    verification_url: str
    user_code: str


@dataclass(frozen=True)
class TurnHandle:
    """Identifiers needed to observe or interrupt one Codex turn."""

    thread_id: str
    turn_id: str


@dataclass(frozen=True)
class CodexEvent:
    """One server notification emitted while a Codex turn runs."""

    method: str
    params: dict[str, object]

    @property
    def text_delta(self) -> str | None:
        delta = self.params.get("delta")
        return (
            delta if self.method == "item/agentMessage/delta" and isinstance(delta, str) else None
        )

    @property
    def terminal_status(self) -> str | None:
        if self.method != "turn/completed":
            return None
        turn = self.params.get("turn")
        if not isinstance(turn, dict):
            return None
        status = turn.get("status")
        return status if isinstance(status, str) else None


class CodexAppServerClient:
    """Own one Codex app-server subprocess and its JSONL connection."""

    def __init__(self, command: Sequence[str] = ("codex", "app-server")) -> None:
        self._command = tuple(command)
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[dict[str, object]]] = {}
        self._notifications: asyncio.Queue[dict[str, object] | CodexAppServerError] = (
            asyncio.Queue()
        )
        self._next_request_id = 1
        self._closing = False
        self.server_info: dict[str, object] | None = None

    async def __aenter__(self) -> "CodexAppServerClient":
        await self.start()
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.close()

    async def start(self) -> None:
        if self._process is not None:
            raise CodexAppServerError("Codex app-server is already running")
        self._closing = False
        self._process = await asyncio.create_subprocess_exec(
            *self._command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._reader_task = asyncio.create_task(self._read_messages())
        self._stderr_task = asyncio.create_task(self._drain_stderr(self._process))
        try:
            result = await self._request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "langgraph_practice",
                        "title": "LangGraph Practice",
                        "version": "0.1.0",
                    }
                },
                request_id=0,
            )
            self.server_info = result
            await self._send({"method": "initialized", "params": {}})
        except BaseException:
            await self.close()
            raise

    async def read_account(self) -> dict[str, object]:
        """Return the active Codex account and authentication requirement."""
        return await self._request("account/read", {"refreshToken": False})

    async def start_device_code_login(self) -> DeviceCodeLogin:
        """Begin Codex-managed ChatGPT authentication."""
        result = await self._request("account/login/start", {"type": "chatgptDeviceCode"})
        login_id = _required_string(result, "loginId", "Device-code login")
        verification_url = _required_string(result, "verificationUrl", "Device-code login")
        user_code = _required_string(result, "userCode", "Device-code login")
        return DeviceCodeLogin(login_id, verification_url, user_code)

    async def wait_for_login(self, login_id: str) -> None:
        """Wait until the requested ChatGPT login succeeds or fails."""
        while True:
            message = await self._next_notification()
            if message.get("method") != "account/login/completed":
                continue
            params = message.get("params")
            if not isinstance(params, dict) or params.get("loginId") != login_id:
                continue
            if params.get("success") is True:
                return
            error = params.get("error")
            raise CodexAppServerError(f"ChatGPT login failed: {error or 'unknown error'}")

    async def start_thread(self, *, cwd: str) -> str:
        """Start a persisted Codex thread rooted at cwd."""
        result = await self._request("thread/start", {"cwd": cwd, "ephemeral": False})
        return _nested_id(result, "thread", "Thread start")

    async def resume_thread(self, thread_id: str) -> str:
        """Resume a persisted Codex thread by id."""
        result = await self._request("thread/resume", {"threadId": thread_id})
        return _nested_id(result, "thread", "Thread resume")

    async def start_turn(self, thread_id: str, prompt: str) -> TurnHandle:
        """Start one text turn on an existing Codex thread."""
        result = await self._request(
            "turn/start",
            {"threadId": thread_id, "input": [{"type": "text", "text": prompt}]},
        )
        return TurnHandle(thread_id, _nested_id(result, "turn", "Turn start"))

    async def stream_turn(self, turn: TurnHandle) -> AsyncIterator[CodexEvent]:
        """Yield notifications for a turn through its terminal status."""
        while True:
            message = await self._next_notification()
            method = message.get("method")
            params = message.get("params")
            if not isinstance(method, str) or not isinstance(params, dict):
                continue
            if not _belongs_to_turn(params, turn):
                continue
            event = CodexEvent(method, params)
            if method == "turn/completed" and event.terminal_status is None:
                raise CodexAppServerError(
                    "Codex app-server emitted turn/completed without a terminal status"
                )
            yield event
            if event.terminal_status is not None:
                return

    async def interrupt_turn(self, turn: TurnHandle) -> None:
        """Request interruption of an in-flight Codex turn."""
        await self._request("turn/interrupt", {"threadId": turn.thread_id, "turnId": turn.turn_id})

    async def close(self) -> None:
        process = self._process
        reader_task = self._reader_task
        stderr_task = self._stderr_task
        self._process = None
        self._reader_task = None
        self._stderr_task = None
        self.server_info = None
        if process is None:
            return
        self._closing = True
        if process.stdin is not None:
            process.stdin.close()
            with suppress(BrokenPipeError, ConnectionResetError):
                await process.stdin.wait_closed()
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except TimeoutError:
            process.terminate()
            await process.wait()
        if reader_task is not None:
            with suppress(CodexAppServerError):
                await reader_task
        if stderr_task is not None:
            await stderr_task

    async def _request(
        self, method: str, params: dict[str, object], *, request_id: int | None = None
    ) -> dict[str, object]:
        if request_id is None:
            request_id = self._next_request_id
            self._next_request_id += 1
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._send({"method": method, "id": request_id, "params": params})
            response = await future
        finally:
            self._pending.pop(request_id, None)
        error = response.get("error")
        if error is not None:
            raise CodexAppServerError(f"Codex request {method} failed: {error}")
        result = response.get("result")
        if not isinstance(result, dict):
            raise CodexAppServerError(f"Codex request {method} returned an invalid result")
        return result

    async def _send(self, message: dict[str, object]) -> None:
        process = self._require_process()
        if process.stdin is None:
            raise CodexAppServerError("Codex app-server stdin is unavailable")
        process.stdin.write(json.dumps(message).encode() + b"\n")
        try:
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise CodexAppServerError("Codex app-server closed stdin unexpectedly") from exc

    async def _read_messages(self) -> None:
        try:
            while True:
                message = await self._read_message()
                response_id = message.get("id")
                if isinstance(response_id, int) and response_id in self._pending:
                    future = self._pending[response_id]
                    if not future.done():
                        future.set_result(message)
                elif isinstance(message.get("method"), str):
                    await self._notifications.put(message)
                else:
                    raise CodexAppServerError(
                        "Codex app-server emitted an invalid protocol message"
                    )
        except CodexAppServerError as exc:
            if self._closing:
                return
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(exc)
            await self._notifications.put(exc)

    async def _drain_stderr(self, process: asyncio.subprocess.Process) -> None:
        if process.stderr is None:
            return
        while await process.stderr.read(8192):
            pass

    async def _read_message(self) -> dict[str, object]:
        process = self._require_process()
        if process.stdout is None:
            raise CodexAppServerError("Codex app-server stdout is unavailable")
        line = await process.stdout.readline()
        if not line:
            raise CodexAppServerError("Codex app-server closed stdout unexpectedly")
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CodexAppServerError("Codex app-server emitted malformed JSON") from exc
        if not isinstance(message, dict):
            raise CodexAppServerError("Codex app-server emitted a non-object message")
        return message

    async def _next_notification(self) -> dict[str, object]:
        message = await self._notifications.get()
        if isinstance(message, CodexAppServerError):
            raise message
        return message

    def _require_process(self) -> asyncio.subprocess.Process:
        if self._process is None:
            raise CodexAppServerError("Codex app-server is not running")
        return self._process


def _required_string(value: dict[str, object], key: str, operation: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise CodexAppServerError(f"{operation} response has no {key}")
    return result


def _nested_id(value: dict[str, object], key: str, operation: str) -> str:
    nested = value.get(key)
    if not isinstance(nested, dict):
        raise CodexAppServerError(f"{operation} response has no {key}")
    return _required_string(nested, "id", operation)


def _belongs_to_turn(params: dict[str, object], turn: TurnHandle) -> bool:
    if params.get("threadId") != turn.thread_id:
        return False
    turn_id = params.get("turnId")
    if turn_id is None:
        nested_turn = params.get("turn")
        turn_id = nested_turn.get("id") if isinstance(nested_turn, dict) else None
    return turn_id == turn.turn_id

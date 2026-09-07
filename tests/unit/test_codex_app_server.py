"""Public-seam tests for the Codex app-server JSONL client."""

import asyncio
import sys
from pathlib import Path

import pytest

from codex_app_server import (
    CodexAppServerClient,
    CodexAppServerError,
    DeviceCodeLogin,
    TurnHandle,
)

FAKE_SERVER = Path(__file__).parents[1] / "fixtures" / "fake_codex_app_server.py"


async def test_client_completes_app_server_handshake() -> None:
    async with CodexAppServerClient(command=(sys.executable, str(FAKE_SERVER))) as client:
        assert client.server_info == {
            "userAgent": "fake-codex/1.0",
            "platformFamily": "unix",
            "platformOs": "test",
        }


async def test_client_exposes_device_code_login() -> None:
    async with CodexAppServerClient(command=(sys.executable, str(FAKE_SERVER))) as client:
        assert await client.read_account() == {"account": None, "requiresOpenaiAuth": True}

        login = await client.start_device_code_login()

        assert login == DeviceCodeLogin(
            login_id="login-1",
            verification_url="https://auth.openai.com/codex/device",
            user_code="ABCD-1234",
        )
        await client.wait_for_login(login.login_id)


async def test_client_starts_resumes_and_streams_a_thread() -> None:
    async with CodexAppServerClient(command=(sys.executable, str(FAKE_SERVER))) as client:
        thread_id = await client.start_thread(cwd="/workspace")
        turn = await client.start_turn(thread_id, "Say hello")
        events = [event async for event in client.stream_turn(turn)]

        assert thread_id == "thread-1"
        assert turn.turn_id == "turn-1"
        assert [event.method for event in events] == [
            "item/agentMessage/delta",
            "turn/completed",
        ]
        assert events[0].text_delta == "hello"
        assert events[-1].terminal_status == "completed"

    async with CodexAppServerClient(command=(sys.executable, str(FAKE_SERVER))) as restarted:
        assert await restarted.resume_thread(thread_id) == thread_id


async def test_client_can_interrupt_a_turn() -> None:
    async with CodexAppServerClient(command=(sys.executable, str(FAKE_SERVER))) as client:
        await client.interrupt_turn(TurnHandle("thread-1", "turn-1"))


@pytest.mark.parametrize("status", ["failed", "interrupted"])
async def test_client_recognizes_non_success_terminal_status(status: str) -> None:
    async with CodexAppServerClient(
        command=(sys.executable, str(FAKE_SERVER), f"turn-{status}")
    ) as client:
        turn = await client.start_turn("thread-1", "Stop")
        events = [event async for event in client.stream_turn(turn)]

    assert events[-1].terminal_status == status


async def test_client_rejects_malformed_terminal_event_without_hanging() -> None:
    async with CodexAppServerClient(
        command=(sys.executable, str(FAKE_SERVER), "malformed-terminal")
    ) as client:
        turn = await client.start_turn("thread-1", "Stop")
        with pytest.raises(CodexAppServerError, match="terminal status"):
            await asyncio.wait_for(anext(client.stream_turn(turn)), timeout=1)


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("malformed", "malformed JSON"),
        ("premature-eof", "closed stdout unexpectedly"),
    ],
)
async def test_client_surfaces_protocol_failure_without_hanging(mode: str, message: str) -> None:
    client = CodexAppServerClient(command=(sys.executable, str(FAKE_SERVER), mode))
    try:
        with pytest.raises(CodexAppServerError, match=message):
            await asyncio.wait_for(client.start(), timeout=1)
    finally:
        await client.close()


async def test_client_surfaces_json_rpc_error() -> None:
    async with CodexAppServerClient(
        command=(sys.executable, str(FAKE_SERVER), "request-error")
    ) as client:
        with pytest.raises(CodexAppServerError, match="account/read.*denied"):
            await client.read_account()


async def test_client_rejects_invalid_protocol_envelope_without_hanging() -> None:
    async with CodexAppServerClient(
        command=(sys.executable, str(FAKE_SERVER), "invalid-envelope")
    ) as client:
        with pytest.raises(CodexAppServerError, match="invalid protocol message"):
            await asyncio.wait_for(client.read_account(), timeout=1)


async def test_client_drains_child_process_stderr() -> None:
    async with CodexAppServerClient(
        command=(sys.executable, str(FAKE_SERVER), "noisy-stderr")
    ) as client:
        account = await asyncio.wait_for(client.read_account(), timeout=1)

    assert account["requiresOpenaiAuth"] is True

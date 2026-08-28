"""Seam: handle_slack_message (DM event in, post_reply/set_status calls out)."""

import asyncio

from conftest import build_test_graph, build_test_settings
from langchain_core.messages import AIMessage

from slack_app import InFlightTurns, handle_slack_message


async def test_replies_to_a_top_level_dm_using_its_own_ts_as_thread_id() -> None:
    graph = build_test_graph([AIMessage(content="Hello! How can I help?")])
    settings = build_test_settings()
    calls: list[tuple[str, str, str]] = []
    statuses: list[tuple[str, str, str]] = []
    in_flight: InFlightTurns = {}
    event = {"user": "U_ALLOWED", "channel": "D1", "ts": "111.1", "text": "hi"}

    async def post_reply(channel: str, thread_ts: str, text: str) -> None:
        calls.append((channel, thread_ts, text))

    async def set_status(channel: str, thread_ts: str, status: str) -> None:
        statuses.append((channel, thread_ts, status))

    await handle_slack_message(
        event,
        graph=graph,
        settings=settings,
        post_reply=post_reply,
        set_status=set_status,
        in_flight=in_flight,
    )

    assert calls == [("D1", "111.1", "Hello! How can I help?")]
    assert statuses == [("D1", "111.1", "思考中…")]
    assert in_flight == {}


async def test_replies_within_the_same_slack_thread_continue_the_same_conversation() -> None:
    graph = build_test_graph(
        [AIMessage(content="I'll remember that."), AIMessage(content="You like tea.")]
    )
    settings = build_test_settings()
    calls: list[tuple[str, str, str]] = []
    in_flight: InFlightTurns = {}
    first_event = {"user": "U_ALLOWED", "channel": "D1", "ts": "111.1", "text": "I like tea"}
    reply_event = {
        "user": "U_ALLOWED",
        "channel": "D1",
        "ts": "111.2",
        "thread_ts": "111.1",
        "text": "what do I like?",
    }

    async def post_reply(channel: str, thread_ts: str, text: str) -> None:
        calls.append((channel, thread_ts, text))

    async def set_status(channel: str, thread_ts: str, status: str) -> None:
        pass

    await handle_slack_message(
        first_event,
        graph=graph,
        settings=settings,
        post_reply=post_reply,
        set_status=set_status,
        in_flight=in_flight,
    )
    await handle_slack_message(
        reply_event,
        graph=graph,
        settings=settings,
        post_reply=post_reply,
        set_status=set_status,
        in_flight=in_flight,
    )

    assert calls == [
        ("D1", "111.1", "I'll remember that."),
        ("D1", "111.1", "You like tea."),
    ]
    state = graph.get_state({"configurable": {"thread_id": "111.1", "user_id": "the-user"}})
    assert len(state.values["messages"]) == 4


async def test_ignores_dms_from_non_whitelisted_users() -> None:
    graph = build_test_graph([AIMessage(content="should not be called")])
    settings = build_test_settings()
    calls: list[tuple[str, str, str]] = []
    statuses: list[tuple[str, str, str]] = []
    in_flight: InFlightTurns = {}
    event = {"user": "U_STRANGER", "channel": "D1", "ts": "111.1", "text": "hi"}

    async def post_reply(channel: str, thread_ts: str, text: str) -> None:
        calls.append((channel, thread_ts, text))

    async def set_status(channel: str, thread_ts: str, status: str) -> None:
        statuses.append((channel, thread_ts, status))

    await handle_slack_message(
        event,
        graph=graph,
        settings=settings,
        post_reply=post_reply,
        set_status=set_status,
        in_flight=in_flight,
    )

    assert calls == []
    assert statuses == []


async def test_replies_with_a_short_error_message_when_the_graph_raises(monkeypatch) -> None:
    graph = build_test_graph([AIMessage(content="unused")])

    async def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("LM Studio is unreachable")

    monkeypatch.setattr(graph, "ainvoke", boom)
    settings = build_test_settings()
    calls: list[tuple[str, str, str]] = []
    in_flight: InFlightTurns = {}
    event = {"user": "U_ALLOWED", "channel": "D1", "ts": "111.1", "text": "hi"}

    async def post_reply(channel: str, thread_ts: str, text: str) -> None:
        calls.append((channel, thread_ts, text))

    async def set_status(channel: str, thread_ts: str, status: str) -> None:
        pass

    await handle_slack_message(
        event,
        graph=graph,
        settings=settings,
        post_reply=post_reply,
        set_status=set_status,
        in_flight=in_flight,
    )

    assert len(calls) == 1
    channel, thread_id, text = calls[0]
    assert (channel, thread_id) == ("D1", "111.1")
    assert "錯誤" in text
    assert in_flight == {}


async def test_a_followup_message_cancels_the_in_flight_turn_and_replies_once() -> None:
    graph = build_test_graph([AIMessage(content="unused")])
    settings = build_test_settings()
    calls: list[tuple[str, str, str]] = []
    statuses: list[tuple[str, str, str]] = []
    in_flight: InFlightTurns = {}
    started = asyncio.Event()
    call_count = 0

    async def fake_ainvoke(input: dict, config: dict) -> dict:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            started.set()
            await asyncio.sleep(3600)
            raise AssertionError("first turn should have been cancelled")
        return {"messages": [AIMessage(content="the combined reply")]}

    graph.ainvoke = fake_ainvoke  # type: ignore[method-assign]

    async def post_reply(channel: str, thread_ts: str, text: str) -> None:
        calls.append((channel, thread_ts, text))

    async def set_status(channel: str, thread_ts: str, status: str) -> None:
        statuses.append((channel, thread_ts, status))

    first_event = {"user": "U_ALLOWED", "channel": "D1", "ts": "111.1", "text": "first thought"}
    second_event = {
        "user": "U_ALLOWED",
        "channel": "D1",
        "ts": "111.2",
        "thread_ts": "111.1",
        "text": "actually, one more thing",
    }

    first_turn = asyncio.create_task(
        handle_slack_message(
            first_event,
            graph=graph,
            settings=settings,
            post_reply=post_reply,
            set_status=set_status,
            in_flight=in_flight,
        )
    )
    await started.wait()

    await handle_slack_message(
        second_event,
        graph=graph,
        settings=settings,
        post_reply=post_reply,
        set_status=set_status,
        in_flight=in_flight,
    )
    await first_turn

    assert calls == [("D1", "111.1", "the combined reply")]
    assert statuses == [
        ("D1", "111.1", "思考中…"),
        ("D1", "111.1", "已收到你的補充，重新整理回覆中…"),
    ]
    assert in_flight == {}


async def test_in_flight_turns_for_different_conversations_do_not_interfere() -> None:
    graph = build_test_graph([AIMessage(content="unused")])
    settings = build_test_settings()
    calls: list[tuple[str, str, str]] = []
    in_flight: InFlightTurns = {}
    started = asyncio.Event()
    hold = asyncio.Event()

    async def fake_ainvoke(input: dict, config: dict) -> dict:
        thread_id = config["configurable"]["thread_id"]
        if thread_id == "111.1":
            started.set()
            await hold.wait()
            return {"messages": [AIMessage(content="reply for 111.1")]}
        return {"messages": [AIMessage(content=f"reply for {thread_id}")]}

    graph.ainvoke = fake_ainvoke  # type: ignore[method-assign]

    async def post_reply(channel: str, thread_ts: str, text: str) -> None:
        calls.append((channel, thread_ts, text))

    async def set_status(channel: str, thread_ts: str, status: str) -> None:
        pass

    slow_event = {"user": "U_ALLOWED", "channel": "D1", "ts": "111.1", "text": "slow"}
    other_event = {"user": "U_ALLOWED", "channel": "D1", "ts": "222.1", "text": "unrelated"}

    slow_turn = asyncio.create_task(
        handle_slack_message(
            slow_event,
            graph=graph,
            settings=settings,
            post_reply=post_reply,
            set_status=set_status,
            in_flight=in_flight,
        )
    )
    await started.wait()

    await handle_slack_message(
        other_event,
        graph=graph,
        settings=settings,
        post_reply=post_reply,
        set_status=set_status,
        in_flight=in_flight,
    )

    assert calls == [("D1", "222.1", "reply for 222.1")]
    assert list(in_flight) == ["111.1"]

    hold.set()
    await slow_turn

    assert calls == [
        ("D1", "222.1", "reply for 222.1"),
        ("D1", "111.1", "reply for 111.1"),
    ]
    assert in_flight == {}

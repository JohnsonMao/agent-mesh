"""Seam: handle_slack_message (DM event in, open_thinking_stream calls out)."""

import asyncio

from conftest import build_test_graph, build_test_settings
from langchain_core.messages import AIMessage, ToolMessage
from slack_sdk.models.blocks.block_elements import UrlSourceElement

from slack_app import InFlightTurns, handle_slack_message


class FakeThinkingStream:
    def __init__(self) -> None:
        self.updates: list[tuple[str, str, str, str | None, list[dict] | None]] = []
        self.finished: str | None = None

    async def update_task(
        self,
        task_id: str,
        title: str,
        status: str,
        output: str | None = None,
        sources: list[UrlSourceElement] | None = None,
    ) -> None:
        self.updates.append((task_id, title, status, output, sources))

    async def finish(self, markdown_text: str) -> None:
        self.finished = markdown_text


class FakeOpenThinkingStream:
    """Records (channel, thread_id) per call and hands out one fake stream each time."""

    def __init__(self) -> None:
        self.opened: list[tuple[str, str]] = []
        self.streams: list[FakeThinkingStream] = []

    async def __call__(self, channel: str, thread_id: str) -> FakeThinkingStream:
        self.opened.append((channel, thread_id))
        stream = FakeThinkingStream()
        self.streams.append(stream)
        return stream


def _fake_astream_events(tool_calls: list[tuple[str, str, str, list[dict]]]):
    """tool_calls: (run_id, tool_name, content, artifact), yielded start then end per call."""

    async def astream_events(input: dict, config: dict, **kwargs: object):
        for run_id, name, content, artifact in tool_calls:
            yield {"event": "on_tool_start", "name": name, "run_id": run_id}
            yield {
                "event": "on_tool_end",
                "name": name,
                "run_id": run_id,
                "data": {
                    "output": ToolMessage(
                        content=content, name=name, tool_call_id=run_id, artifact=artifact
                    )
                },
            }

    return astream_events


def _fake_aget_state(content: str):
    async def aget_state(config: dict):
        class _State:
            values = {"messages": [AIMessage(content=content)]}

        return _State()

    return aget_state


async def test_replies_to_a_top_level_dm_using_its_own_ts_as_thread_id() -> None:
    graph = build_test_graph([AIMessage(content="Hello! How can I help?")])
    settings = build_test_settings()
    open_thinking_stream = FakeOpenThinkingStream()
    in_flight: InFlightTurns = {}
    event = {"user": "U_ALLOWED", "channel": "D1", "ts": "111.1", "text": "hi"}

    await handle_slack_message(
        event,
        graph=graph,
        settings=settings,
        open_thinking_stream=open_thinking_stream,
        in_flight=in_flight,
    )

    assert open_thinking_stream.opened == [("D1", "111.1")]
    (stream,) = open_thinking_stream.streams
    assert stream.updates == []
    assert stream.finished == "Hello! How can I help?"
    assert in_flight == {}


async def test_a_tool_call_updates_its_task_from_in_progress_to_complete() -> None:
    graph = build_test_graph([AIMessage(content="unused")])
    graph.astream_events = _fake_astream_events(  # type: ignore[method-assign]
        [("run-1", "save_memory", "Saved memory: I like tea", [])]
    )
    graph.aget_state = _fake_aget_state("Got it, I'll remember that.")  # type: ignore[method-assign]
    settings = build_test_settings()
    open_thinking_stream = FakeOpenThinkingStream()
    in_flight: InFlightTurns = {}
    event = {"user": "U_ALLOWED", "channel": "D1", "ts": "111.1", "text": "I like tea"}

    await handle_slack_message(
        event,
        graph=graph,
        settings=settings,
        open_thinking_stream=open_thinking_stream,
        in_flight=in_flight,
    )

    (stream,) = open_thinking_stream.streams
    assert stream.updates == [
        ("run-1", "📝 正在記下新的一件事…", "in_progress", None, None),
        ("run-1", "📝 記住新事項", "complete", "I like tea", None),
    ]
    assert stream.finished == "Got it, I'll remember that."


async def test_web_search_task_card_gets_titles_as_output_and_urls_as_sources() -> None:
    graph = build_test_graph([AIMessage(content="unused")])
    graph.astream_events = _fake_astream_events(  # type: ignore[method-assign]
        [
            (
                "run-1",
                "web_search",
                "Weather: Sunny all day (http://example.com)",
                [{"title": "Weather", "url": "http://example.com"}],
            )
        ]
    )
    graph.aget_state = _fake_aget_state("It's sunny today.")  # type: ignore[method-assign]
    settings = build_test_settings()
    open_thinking_stream = FakeOpenThinkingStream()
    in_flight: InFlightTurns = {}
    event = {"user": "U_ALLOWED", "channel": "D1", "ts": "111.1", "text": "weather?"}

    await handle_slack_message(
        event,
        graph=graph,
        settings=settings,
        open_thinking_stream=open_thinking_stream,
        in_flight=in_flight,
    )

    (stream,) = open_thinking_stream.streams
    _, _, _, output, sources = stream.updates[1]
    assert output == "Weather"
    assert sources == [UrlSourceElement(url="http://example.com", text="Weather")]


async def test_multiple_different_tool_calls_each_get_their_own_stacked_task_card() -> None:
    graph = build_test_graph([AIMessage(content="unused")])
    graph.astream_events = _fake_astream_events(  # type: ignore[method-assign]
        [
            ("run-1", "save_memory", "Saved memory: I like tea", []),
            ("run-2", "recall_memory", "User likes tea", []),
        ]
    )
    graph.aget_state = _fake_aget_state("Done.")  # type: ignore[method-assign]
    settings = build_test_settings()
    open_thinking_stream = FakeOpenThinkingStream()
    in_flight: InFlightTurns = {}
    event = {"user": "U_ALLOWED", "channel": "D1", "ts": "111.1", "text": "hi"}

    await handle_slack_message(
        event,
        graph=graph,
        settings=settings,
        open_thinking_stream=open_thinking_stream,
        in_flight=in_flight,
    )

    (stream,) = open_thinking_stream.streams
    task_ids_in_order = [u[0] for u in stream.updates]
    assert task_ids_in_order == ["run-1", "run-1", "run-2", "run-2"]


async def test_ignores_dms_from_non_whitelisted_users() -> None:
    graph = build_test_graph([AIMessage(content="should not be called")])
    settings = build_test_settings()
    open_thinking_stream = FakeOpenThinkingStream()
    in_flight: InFlightTurns = {}
    event = {"user": "U_STRANGER", "channel": "D1", "ts": "111.1", "text": "hi"}

    await handle_slack_message(
        event,
        graph=graph,
        settings=settings,
        open_thinking_stream=open_thinking_stream,
        in_flight=in_flight,
    )

    assert open_thinking_stream.opened == []


async def test_replies_with_a_short_error_message_when_the_graph_raises(monkeypatch) -> None:
    graph = build_test_graph([AIMessage(content="unused")])

    async def boom(*args: object, **kwargs: object):
        raise RuntimeError("LM Studio is unreachable")
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(graph, "astream_events", boom)
    settings = build_test_settings()
    open_thinking_stream = FakeOpenThinkingStream()
    in_flight: InFlightTurns = {}
    event = {"user": "U_ALLOWED", "channel": "D1", "ts": "111.1", "text": "hi"}

    await handle_slack_message(
        event,
        graph=graph,
        settings=settings,
        open_thinking_stream=open_thinking_stream,
        in_flight=in_flight,
    )

    (stream,) = open_thinking_stream.streams
    assert stream.finished is not None
    assert "錯誤" in stream.finished
    assert in_flight == {}


async def test_replies_within_the_same_slack_thread_continue_the_same_conversation() -> None:
    graph = build_test_graph(
        [AIMessage(content="I'll remember that."), AIMessage(content="You like tea.")]
    )
    settings = build_test_settings()
    open_thinking_stream = FakeOpenThinkingStream()
    in_flight: InFlightTurns = {}
    first_event = {"user": "U_ALLOWED", "channel": "D1", "ts": "111.1", "text": "I like tea"}
    reply_event = {
        "user": "U_ALLOWED",
        "channel": "D1",
        "ts": "111.2",
        "thread_ts": "111.1",
        "text": "what do I like?",
    }

    await handle_slack_message(
        first_event,
        graph=graph,
        settings=settings,
        open_thinking_stream=open_thinking_stream,
        in_flight=in_flight,
    )
    await handle_slack_message(
        reply_event,
        graph=graph,
        settings=settings,
        open_thinking_stream=open_thinking_stream,
        in_flight=in_flight,
    )

    assert [s.finished for s in open_thinking_stream.streams] == [
        "I'll remember that.",
        "You like tea.",
    ]
    state = graph.get_state({"configurable": {"thread_id": "111.1", "user_id": "the-user"}})
    assert len(state.values["messages"]) == 4


async def test_a_followup_message_cancels_the_in_flight_turn_and_starts_a_fresh_stream() -> None:
    graph = build_test_graph([AIMessage(content="unused")])
    settings = build_test_settings()
    open_thinking_stream = FakeOpenThinkingStream()
    in_flight: InFlightTurns = {}
    started = asyncio.Event()
    call_count = 0

    async def fake_astream_events(input: dict, config: dict, **kwargs: object):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            started.set()
            await asyncio.sleep(3600)
            raise AssertionError("first turn should have been cancelled")
        return
        yield  # pragma: no cover - makes this an async generator

    graph.astream_events = fake_astream_events  # type: ignore[method-assign]
    graph.aget_state = _fake_aget_state("the combined reply")  # type: ignore[method-assign]

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
            open_thinking_stream=open_thinking_stream,
            in_flight=in_flight,
        )
    )
    await started.wait()

    await handle_slack_message(
        second_event,
        graph=graph,
        settings=settings,
        open_thinking_stream=open_thinking_stream,
        in_flight=in_flight,
    )
    await first_turn

    assert len(open_thinking_stream.streams) == 2
    first_stream, second_stream = open_thinking_stream.streams
    assert first_stream.finished == "已收到你的補充，重新整理回覆中…"
    assert second_stream.finished == "the combined reply"
    assert in_flight == {}


async def test_in_flight_turns_for_different_conversations_do_not_interfere() -> None:
    graph = build_test_graph([AIMessage(content="unused")])
    settings = build_test_settings()
    open_thinking_stream = FakeOpenThinkingStream()
    in_flight: InFlightTurns = {}
    started = asyncio.Event()
    hold = asyncio.Event()

    async def fake_astream_events(input: dict, config: dict, **kwargs: object):
        thread_id = config["configurable"]["thread_id"]
        if thread_id == "111.1":
            started.set()
            await hold.wait()
        return
        yield  # pragma: no cover - makes this an async generator

    graph.astream_events = fake_astream_events  # type: ignore[method-assign]

    async def fake_aget_state(config: dict):
        thread_id = config["configurable"]["thread_id"]

        class _State:
            values = {"messages": [AIMessage(content=f"reply for {thread_id}")]}

        return _State()

    graph.aget_state = fake_aget_state  # type: ignore[method-assign]

    slow_event = {"user": "U_ALLOWED", "channel": "D1", "ts": "111.1", "text": "slow"}
    other_event = {"user": "U_ALLOWED", "channel": "D1", "ts": "222.1", "text": "unrelated"}

    slow_turn = asyncio.create_task(
        handle_slack_message(
            slow_event,
            graph=graph,
            settings=settings,
            open_thinking_stream=open_thinking_stream,
            in_flight=in_flight,
        )
    )
    await started.wait()

    await handle_slack_message(
        other_event,
        graph=graph,
        settings=settings,
        open_thinking_stream=open_thinking_stream,
        in_flight=in_flight,
    )

    assert open_thinking_stream.streams[-1].finished == "reply for 222.1"
    assert list(in_flight) == ["111.1"]

    hold.set()
    await slow_turn

    assert open_thinking_stream.streams[0].finished == "reply for 111.1"
    assert in_flight == {}

"""Seam: handle_slack_message (DM event in, open_thinking_stream calls out)."""

import asyncio
from datetime import UTC, datetime, timedelta

from conftest import build_test_graph, build_test_settings
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from slack_sdk.models.blocks.block_elements import UrlSourceElement

from execution_traces import BoundedBatchExporter, InMemorySpanExporter, TraceRecorder
from slack_app import InFlightTurns, PausedTurn, handle_paused_turn_action, handle_slack_message


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


class FailingFirstFinishStream(FakeOpenThinkingStream):
    """Makes cancellation cleanup fail once, like a transient chat.stopStream failure."""

    async def __call__(self, channel: str, thread_id: str) -> FakeThinkingStream:
        stream = await super().__call__(channel, thread_id)
        if len(self.streams) == 1:
            original_finish = stream.finish

            async def failing_finish(markdown_text: str) -> None:
                await original_finish(markdown_text)
                raise RuntimeError("simulated chat.stopStream 500")

            stream.finish = failing_finish  # type: ignore[method-assign]
        return stream


class FakeSetStatus:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def __call__(self, channel: str, thread_id: str, status: str) -> None:
        self.calls.append((channel, thread_id, status))


class FakePostReply:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def __call__(self, channel: str, thread_id: str, text: str) -> None:
        self.calls.append((channel, thread_id, text))


class FakeDownloadImage:
    """Records which Slack file objects were requested and hands back scripted bytes."""

    def __init__(self, results: dict[str, bytes] | None = None) -> None:
        self.results = results or {}
        self.calls: list[dict] = []

    async def __call__(self, file: dict) -> bytes:
        self.calls.append(file)
        if file["id"] not in self.results:
            raise RuntimeError(f"no fake download configured for {file['id']}")
        return self.results[file["id"]]


async def test_only_the_allowed_user_can_stop_the_current_paused_turn() -> None:
    cancelled = False

    async def cancel() -> None:
        nonlocal cancelled
        cancelled = True

    async def resume() -> None:
        raise AssertionError("stop must not resume")

    paused_turns = {
        "111.1": PausedTurn(
            allowed_user_id="U_ALLOWED",
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
            cancel=cancel,
            resume=resume,
        )
    }

    rejected = await handle_paused_turn_action(
        thread_id="111.1", user_id="U_STRANGER", action="stop", paused_turns=paused_turns
    )
    accepted = await handle_paused_turn_action(
        thread_id="111.1", user_id="U_ALLOWED", action="stop", paused_turns=paused_turns
    )

    assert rejected == "你沒有操作這個回合的權限。"
    assert accepted == "已停止這個回合。"
    assert cancelled is True
    assert paused_turns == {}


def _fake_astream_events(
    tool_calls: list[tuple[str, str, str, list[dict]]]
    | list[tuple[str, str, str, list[dict], dict]],
):
    """tool_calls: (run_id, tool_name, content, artifact) or (run_id, name, content, artifact, input)."""

    async def astream_events(input: dict, config: dict, **kwargs: object):
        for item in tool_calls:
            run_id, name, content, artifact = item[0], item[1], item[2], item[3]
            tool_input = item[4] if len(item) > 4 else {}
            yield {
                "event": "on_tool_start",
                "name": name,
                "run_id": run_id,
                "data": {"input": tool_input},
            }
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
    set_status = FakeSetStatus()
    download_image = FakeDownloadImage()
    in_flight: InFlightTurns = {}
    event = {"user": "U_ALLOWED", "channel": "D1", "ts": "111.1", "text": "hi"}

    await handle_slack_message(
        event,
        graph=graph,
        settings=settings,
        set_status=set_status,
        open_thinking_stream=open_thinking_stream,
        download_image=download_image,
        in_flight=in_flight,
    )

    assert open_thinking_stream.opened == [("D1", "111.1")]
    (stream,) = open_thinking_stream.streams
    assert stream.updates == []
    assert stream.finished == "Hello! How can I help?"
    assert set_status.calls == [("D1", "111.1", "思考中…")]
    assert in_flight == {}


async def test_delivers_remaining_long_reply_segments_in_the_same_conversation() -> None:
    reply = "first paragraph\n\n" + "x" * 12_000
    open_thinking_stream = FakeOpenThinkingStream()
    post_reply = FakePostReply()

    await handle_slack_message(
        {"user": "U_ALLOWED", "channel": "D1", "ts": "111.1", "text": "long"},
        graph=build_test_graph([AIMessage(content=reply)]),
        settings=build_test_settings(),
        set_status=FakeSetStatus(),
        open_thinking_stream=open_thinking_stream,
        download_image=FakeDownloadImage(),
        in_flight={},
        post_reply=post_reply,
    )

    assert open_thinking_stream.streams[0].finished is not None
    assert open_thinking_stream.streams[0].finished.startswith("（第 1/")
    assert len(post_reply.calls) == 2
    assert post_reply.calls[0][0:2] == ("D1", "111.1")
    assert post_reply.calls[0][2].startswith("（第 2/")


async def test_a_tool_call_updates_its_task_from_in_progress_to_complete() -> None:
    graph = build_test_graph([AIMessage(content="unused")])
    graph.astream_events = _fake_astream_events(  # type: ignore[method-assign]
        [("run-1", "save_memory", "Saved memory: I like tea", [], {"content": "I like tea"})]
    )
    graph.aget_state = _fake_aget_state("Got it, I'll remember that.")  # type: ignore[method-assign]
    settings = build_test_settings()
    open_thinking_stream = FakeOpenThinkingStream()
    set_status = FakeSetStatus()
    download_image = FakeDownloadImage()
    in_flight: InFlightTurns = {}
    event = {"user": "U_ALLOWED", "channel": "D1", "ts": "111.1", "text": "I like tea"}

    await handle_slack_message(
        event,
        graph=graph,
        settings=settings,
        set_status=set_status,
        open_thinking_stream=open_thinking_stream,
        download_image=download_image,
        in_flight=in_flight,
    )

    (stream,) = open_thinking_stream.streams
    assert stream.updates == [
        ("run-1", "📝 正在記下新的一件事：I like tea…", "in_progress", None, None),
        ("run-1", "📝 記住新事項：I like tea", "complete", "I like tea", None),
    ]
    assert stream.finished == "Got it, I'll remember that."


async def test_records_a_completed_turn_and_its_tool_step_without_changing_slack_reply() -> None:
    graph = build_test_graph([AIMessage(content="unused")])
    graph.astream_events = _fake_astream_events(  # type: ignore[method-assign]
        [("run-1", "execute_command", "x" * 9_000, [], {"command": "echo $TOKEN"})]
    )
    graph.aget_state = _fake_aget_state("done")  # type: ignore[method-assign]
    collector = InMemorySpanExporter()
    exporter = BoundedBatchExporter(collector)
    await handle_slack_message(
        {"user": "U_ALLOWED", "channel": "D1", "ts": "111.1", "text": "TOKEN=secret"},
        graph=graph,
        settings=build_test_settings(),
        set_status=FakeSetStatus(),
        open_thinking_stream=FakeOpenThinkingStream(),
        download_image=FakeDownloadImage(),
        in_flight={},
        trace_recorder=TraceRecorder(exporter),
    )

    await exporter.flush()
    terminal = [span for span in collector.spans if span.ended_at]
    root = next(span for span in terminal if span.category == "turn")
    step = next(span for span in terminal if span.category == "tool")
    assert root.status == "completed"
    assert root.content["output"] == "done"
    assert root.attributes["turn.output.length"] == 4
    assert root.attributes["turn.output.truncated"] is False
    assert root.content["input"] == "TOKEN=[REDACTED]"
    assert step.status == "completed"
    assert len(str(step.content["output"])) == 8_000


async def test_records_model_and_image_analysis_steps() -> None:
    graph = build_test_graph([AIMessage(content="unused")])

    async def events(input: dict, config: dict, **kwargs: object):
        for event_type, name, run_id in (
            ("on_chain_start", "analyze_images", "image"),
            ("on_chain_end", "analyze_images", "image"),
            ("on_chat_model_start", "ChatOpenAI", "model"),
            ("on_chat_model_end", "ChatOpenAI", "model"),
        ):
            yield {"event": event_type, "name": name, "run_id": run_id, "data": {}}

    graph.astream_events = events  # type: ignore[method-assign]
    graph.aget_state = _fake_aget_state("done")  # type: ignore[method-assign]
    collector = InMemorySpanExporter()
    exporter = BoundedBatchExporter(collector)
    await handle_slack_message(
        {"user": "U_ALLOWED", "channel": "D1", "ts": "111.1", "text": "image"},
        graph=graph,
        settings=build_test_settings(),
        set_status=FakeSetStatus(),
        open_thinking_stream=FakeOpenThinkingStream(),
        download_image=FakeDownloadImage(),
        in_flight={},
        trace_recorder=TraceRecorder(exporter),
    )
    await exporter.flush()
    terminal = [span for span in collector.spans if span.ended_at]
    assert [(span.category, span.status) for span in terminal if span.category != "turn"] == [
        ("image_analysis", "completed"),
        ("model", "completed"),
    ]
    root = next(span for span in terminal if span.category == "turn")
    assert root.attributes["turn.model_usage.completeness"] == "unavailable"


async def test_model_usage_and_message_sent_time_are_normalised_for_telemetry() -> None:
    graph = build_test_graph([AIMessage(content="unused")])

    async def events(input: dict, config: dict, **kwargs: object):
        yield {
            "event": "on_chat_model_start",
            "name": "requested-model",
            "run_id": "model",
            "data": {"input": {"sent_at": "must-not-export", "prompt": "hello"}},
        }
        yield {
            "event": "on_chat_model_end",
            "name": "requested-model",
            "run_id": "model",
            "data": {
                "output": AIMessageChunk(
                    content="",
                    response_metadata={"model_name": "provider-model"},
                    usage_metadata={"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
                )
            },
        }

    graph.astream_events = events  # type: ignore[method-assign]
    graph.aget_state = _fake_aget_state("done")  # type: ignore[method-assign]
    collector = InMemorySpanExporter()
    exporter = BoundedBatchExporter(collector)
    await handle_slack_message(
        {"user": "U_ALLOWED", "channel": "D1", "ts": "111.1", "text": "hello"},
        graph=graph,
        settings=build_test_settings(),
        set_status=FakeSetStatus(),
        open_thinking_stream=FakeOpenThinkingStream(),
        download_image=FakeDownloadImage(),
        in_flight={},
        trace_recorder=TraceRecorder(exporter),
    )
    await exporter.flush()

    terminal = [span for span in collector.spans if span.ended_at]
    model = next(span for span in terminal if span.category == "model")
    turn = next(span for span in terminal if span.category == "turn")
    assert model.attributes["llm.model_name"] == "provider-model"
    assert model.attributes["llm.token_count.total"] == 5
    assert turn.attributes["turn.model_usage.completeness"] == "complete"
    assert "sent_at" not in str(model.content)


async def test_telemetry_capture_failure_does_not_change_the_slack_reply() -> None:
    class BrokenRecorder(TraceRecorder):
        async def start_step(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("telemetry extraction failed")

    graph = build_test_graph([AIMessage(content="unused")])
    graph.astream_events = _fake_astream_events(  # type: ignore[method-assign]
        [("run-1", "save_memory", "Saved", [], {})]
    )
    graph.aget_state = _fake_aget_state("reply survives")  # type: ignore[method-assign]
    stream_factory = FakeOpenThinkingStream()
    exporter = BoundedBatchExporter(InMemorySpanExporter())

    await handle_slack_message(
        {"user": "U_ALLOWED", "channel": "D1", "ts": "111.1", "text": "remember this"},
        graph=graph,
        settings=build_test_settings(),
        set_status=FakeSetStatus(),
        open_thinking_stream=stream_factory,
        download_image=FakeDownloadImage(),
        in_flight={},
        trace_recorder=BrokenRecorder(exporter),
    )

    assert stream_factory.streams[0].finished == "reply survives"


async def test_slack_handler_marks_mixed_model_usage_as_partial() -> None:
    graph = build_test_graph([AIMessage(content="unused")])

    async def events(input: dict, config: dict, **kwargs: object):
        for run_id, output in (
            (
                "known",
                AIMessage(
                    content="known",
                    usage_metadata={"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
                ),
            ),
            ("unknown", AIMessage(content="unknown")),
        ):
            yield {"event": "on_chat_model_start", "name": "model", "run_id": run_id, "data": {}}
            yield {
                "event": "on_chat_model_end",
                "name": "model",
                "run_id": run_id,
                "data": {"output": output},
            }

    graph.astream_events = events  # type: ignore[method-assign]
    graph.aget_state = _fake_aget_state("done")  # type: ignore[method-assign]
    collector = InMemorySpanExporter()
    exporter = BoundedBatchExporter(collector)
    await handle_slack_message(
        {"user": "U_ALLOWED", "channel": "D1", "ts": "111.1", "text": "hello"},
        graph=graph,
        settings=build_test_settings(),
        set_status=FakeSetStatus(),
        open_thinking_stream=FakeOpenThinkingStream(),
        download_image=FakeDownloadImage(),
        in_flight={},
        trace_recorder=TraceRecorder(exporter),
    )
    await exporter.flush()

    root = next(span for span in collector.spans if span.category == "turn" and span.ended_at)
    assert root.attributes["turn.model_usage.completeness"] == "partial"
    assert root.attributes["turn.token_count.total"] == 5


async def test_web_search_task_card_gets_titles_as_output_and_urls_as_sources() -> None:
    graph = build_test_graph([AIMessage(content="unused")])
    graph.astream_events = _fake_astream_events(  # type: ignore[method-assign]
        [
            (
                "run-1",
                "web_search",
                "Weather: Sunny all day (http://example.com)",
                [{"title": "Weather", "url": "http://example.com"}],
                {"query": "weather?"},
            )
        ]
    )
    graph.aget_state = _fake_aget_state("It's sunny today.")  # type: ignore[method-assign]
    settings = build_test_settings()
    open_thinking_stream = FakeOpenThinkingStream()
    set_status = FakeSetStatus()
    download_image = FakeDownloadImage()
    in_flight: InFlightTurns = {}
    event = {"user": "U_ALLOWED", "channel": "D1", "ts": "111.1", "text": "weather?"}

    await handle_slack_message(
        event,
        graph=graph,
        settings=settings,
        set_status=set_status,
        open_thinking_stream=open_thinking_stream,
        download_image=download_image,
        in_flight=in_flight,
    )

    (stream,) = open_thinking_stream.streams
    assert stream.updates[0] == (
        "run-1",
        "🔍 正在搜尋網路：weather?…",
        "in_progress",
        None,
        None,
    )
    _, title, status, output, sources = stream.updates[1]
    assert title == "🔍 搜尋網路：weather?"
    assert status == "complete"
    assert output is None
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
    set_status = FakeSetStatus()
    download_image = FakeDownloadImage()
    in_flight: InFlightTurns = {}
    event = {"user": "U_ALLOWED", "channel": "D1", "ts": "111.1", "text": "hi"}

    await handle_slack_message(
        event,
        graph=graph,
        settings=settings,
        set_status=set_status,
        open_thinking_stream=open_thinking_stream,
        download_image=download_image,
        in_flight=in_flight,
    )

    (stream,) = open_thinking_stream.streams
    task_ids_in_order = [u[0] for u in stream.updates]
    assert task_ids_in_order == ["run-1", "run-1", "run-2", "run-2"]


async def test_ignores_dms_from_non_whitelisted_users() -> None:
    graph = build_test_graph([AIMessage(content="should not be called")])
    settings = build_test_settings()
    open_thinking_stream = FakeOpenThinkingStream()
    set_status = FakeSetStatus()
    download_image = FakeDownloadImage()
    in_flight: InFlightTurns = {}
    event = {"user": "U_STRANGER", "channel": "D1", "ts": "111.1", "text": "hi"}

    await handle_slack_message(
        event,
        graph=graph,
        settings=settings,
        set_status=set_status,
        open_thinking_stream=open_thinking_stream,
        download_image=download_image,
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
    set_status = FakeSetStatus()
    download_image = FakeDownloadImage()
    in_flight: InFlightTurns = {}
    event = {"user": "U_ALLOWED", "channel": "D1", "ts": "111.1", "text": "hi"}

    await handle_slack_message(
        event,
        graph=graph,
        settings=settings,
        set_status=set_status,
        open_thinking_stream=open_thinking_stream,
        download_image=download_image,
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
    set_status = FakeSetStatus()
    download_image = FakeDownloadImage()
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
        set_status=set_status,
        open_thinking_stream=open_thinking_stream,
        download_image=download_image,
        in_flight=in_flight,
    )
    await handle_slack_message(
        reply_event,
        graph=graph,
        settings=settings,
        set_status=set_status,
        open_thinking_stream=open_thinking_stream,
        download_image=download_image,
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
    set_status = FakeSetStatus()
    download_image = FakeDownloadImage()
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
            set_status=set_status,
            open_thinking_stream=open_thinking_stream,
            download_image=download_image,
            in_flight=in_flight,
        )
    )
    await started.wait()

    await handle_slack_message(
        second_event,
        graph=graph,
        settings=settings,
        set_status=set_status,
        open_thinking_stream=open_thinking_stream,
        download_image=download_image,
        in_flight=in_flight,
    )
    await first_turn

    assert len(open_thinking_stream.streams) == 2
    first_stream, second_stream = open_thinking_stream.streams
    assert first_stream.finished == "已收到你的補充，重新整理回覆中…"
    assert second_stream.finished == "the combined reply"
    assert set_status.calls == [
        ("D1", "111.1", "思考中…"),
        ("D1", "111.1", "已收到你的補充，重新整理回覆中…"),
    ]
    assert in_flight == {}


async def test_a_followup_starts_even_when_cancelling_the_old_stream_cannot_finish() -> None:
    graph = build_test_graph([AIMessage(content="unused")])
    settings = build_test_settings()
    open_thinking_stream = FailingFirstFinishStream()
    set_status = FakeSetStatus()
    download_image = FakeDownloadImage()
    in_flight: InFlightTurns = {}
    started = asyncio.Event()
    call_count = 0

    async def fake_astream_events(input: dict, config: dict, **kwargs: object):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            started.set()
            await asyncio.sleep(3600)
        return
        yield  # pragma: no cover - makes this an async generator

    graph.astream_events = fake_astream_events  # type: ignore[method-assign]
    graph.aget_state = _fake_aget_state("the combined reply")  # type: ignore[method-assign]
    first_event = {"user": "U_ALLOWED", "channel": "D1", "ts": "111.1", "text": "first"}
    followup_event = {
        "user": "U_ALLOWED",
        "channel": "D1",
        "ts": "111.2",
        "thread_ts": "111.1",
        "text": "also this",
    }

    first_turn = asyncio.create_task(
        handle_slack_message(
            first_event,
            graph=graph,
            settings=settings,
            set_status=set_status,
            open_thinking_stream=open_thinking_stream,
            download_image=download_image,
            in_flight=in_flight,
        )
    )
    await started.wait()

    await handle_slack_message(
        followup_event,
        graph=graph,
        settings=settings,
        set_status=set_status,
        open_thinking_stream=open_thinking_stream,
        download_image=download_image,
        in_flight=in_flight,
    )
    await first_turn

    assert len(open_thinking_stream.streams) == 2
    assert open_thinking_stream.streams[-1].finished == "the combined reply"
    assert in_flight == {}


async def test_in_flight_turns_for_different_conversations_do_not_interfere() -> None:
    graph = build_test_graph([AIMessage(content="unused")])
    settings = build_test_settings()
    open_thinking_stream = FakeOpenThinkingStream()
    set_status = FakeSetStatus()
    download_image = FakeDownloadImage()
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
            set_status=set_status,
            open_thinking_stream=open_thinking_stream,
            download_image=download_image,
            in_flight=in_flight,
        )
    )
    await started.wait()

    await handle_slack_message(
        other_event,
        graph=graph,
        settings=settings,
        set_status=set_status,
        open_thinking_stream=open_thinking_stream,
        download_image=download_image,
        in_flight=in_flight,
    )

    assert open_thinking_stream.streams[-1].finished == "reply for 222.1"
    assert list(in_flight) == ["111.1"]

    hold.set()
    await slow_turn

    assert open_thinking_stream.streams[0].finished == "reply for 111.1"
    assert in_flight == {}


async def test_a_message_with_no_files_never_calls_the_downloader() -> None:
    graph = build_test_graph([AIMessage(content="Hello! How can I help?")])
    settings = build_test_settings()
    open_thinking_stream = FakeOpenThinkingStream()
    set_status = FakeSetStatus()
    download_image = FakeDownloadImage()
    in_flight: InFlightTurns = {}
    event = {"user": "U_ALLOWED", "channel": "D1", "ts": "111.1", "text": "hi"}

    await handle_slack_message(
        event,
        graph=graph,
        settings=settings,
        set_status=set_status,
        open_thinking_stream=open_thinking_stream,
        download_image=download_image,
        in_flight=in_flight,
    )

    assert download_image.calls == []


async def test_an_image_attachment_is_downloaded_and_analyzed_as_a_task_card() -> None:
    graph = build_test_graph([AIMessage(content="unused")])
    received_inputs: list[dict] = []

    async def fake_astream_events(input: dict, config: dict, **kwargs: object):
        received_inputs.append(input)
        yield {"event": "on_chain_start", "name": "analyze_images", "run_id": "run-1"}
        yield {"event": "on_chain_end", "name": "analyze_images", "run_id": "run-1"}
        return
        yield  # pragma: no cover - makes this an async generator

    graph.astream_events = fake_astream_events  # type: ignore[method-assign]
    graph.aget_state = _fake_aget_state("It's a cat photo.")  # type: ignore[method-assign]
    settings = build_test_settings()
    open_thinking_stream = FakeOpenThinkingStream()
    set_status = FakeSetStatus()
    download_image = FakeDownloadImage({"F1": b"\x89PNG"})
    in_flight: InFlightTurns = {}
    image_file = {"id": "F1", "mimetype": "image/png", "url_private": "https://x/1.png"}
    event = {
        "user": "U_ALLOWED",
        "channel": "D1",
        "ts": "111.1",
        "text": "what is this?",
        "files": [image_file],
    }

    await handle_slack_message(
        event,
        graph=graph,
        settings=settings,
        set_status=set_status,
        open_thinking_stream=open_thinking_stream,
        download_image=download_image,
        in_flight=in_flight,
    )

    assert download_image.calls == [image_file]
    sent_content = received_inputs[0]["messages"][0].content
    assert sent_content[0] == {"type": "text", "text": "what is this?"}
    assert sent_content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    (stream,) = open_thinking_stream.streams
    assert stream.updates == [
        ("run-1", "🖼️ 正在讀取圖片…", "in_progress", None, None),
        ("run-1", "🖼️ 分析圖片", "complete", None, None),
    ]


async def test_only_the_first_four_image_files_are_downloaded() -> None:
    graph = build_test_graph([AIMessage(content="Hello! How can I help?")])
    settings = build_test_settings()
    open_thinking_stream = FakeOpenThinkingStream()
    set_status = FakeSetStatus()
    files = [{"id": str(i), "mimetype": "image/png", "url_private": "x"} for i in range(6)]
    download_image = FakeDownloadImage({str(i): b"\x89PNG" for i in range(6)})
    in_flight: InFlightTurns = {}
    event = {"user": "U_ALLOWED", "channel": "D1", "ts": "111.1", "text": "hi", "files": files}

    await handle_slack_message(
        event,
        graph=graph,
        settings=settings,
        set_status=set_status,
        open_thinking_stream=open_thinking_stream,
        download_image=download_image,
        in_flight=in_flight,
    )

    assert [f["id"] for f in download_image.calls] == ["0", "1", "2", "3"]


async def test_non_image_file_attachments_are_ignored() -> None:
    graph = build_test_graph([AIMessage(content="Hello! How can I help?")])
    settings = build_test_settings()
    open_thinking_stream = FakeOpenThinkingStream()
    set_status = FakeSetStatus()
    download_image = FakeDownloadImage()
    in_flight: InFlightTurns = {}
    event = {
        "user": "U_ALLOWED",
        "channel": "D1",
        "ts": "111.1",
        "text": "here's a doc",
        "files": [{"id": "F1", "mimetype": "application/pdf", "url_private": "https://x/1.pdf"}],
    }

    await handle_slack_message(
        event,
        graph=graph,
        settings=settings,
        set_status=set_status,
        open_thinking_stream=open_thinking_stream,
        download_image=download_image,
        in_flight=in_flight,
    )

    assert download_image.calls == []


async def test_a_failed_image_download_still_lets_the_turn_proceed() -> None:
    graph = build_test_graph([AIMessage(content="Hello! How can I help?")])
    received_inputs: list[dict] = []

    async def fake_astream_events(input: dict, config: dict, **kwargs: object):
        received_inputs.append(input)
        return
        yield  # pragma: no cover - makes this an async generator

    graph.astream_events = fake_astream_events  # type: ignore[method-assign]
    graph.aget_state = _fake_aget_state("Got it.")  # type: ignore[method-assign]
    settings = build_test_settings()
    open_thinking_stream = FakeOpenThinkingStream()
    set_status = FakeSetStatus()
    download_image = FakeDownloadImage()  # no results configured -> every download fails
    in_flight: InFlightTurns = {}
    event = {
        "user": "U_ALLOWED",
        "channel": "D1",
        "ts": "111.1",
        "text": "look at this",
        "files": [{"id": "F1", "mimetype": "image/png", "url_private": "https://x/1.png"}],
    }

    await handle_slack_message(
        event,
        graph=graph,
        settings=settings,
        set_status=set_status,
        open_thinking_stream=open_thinking_stream,
        download_image=download_image,
        in_flight=in_flight,
    )

    sent_content = received_inputs[0]["messages"][0].content
    assert sent_content == "look at this\n\n[1 張圖片無法讀取]"
    (stream,) = open_thinking_stream.streams
    assert stream.finished == "Got it."


async def test_some_images_failing_to_download_still_analyzes_the_rest() -> None:
    graph = build_test_graph([AIMessage(content="unused")])
    received_inputs: list[dict] = []

    async def fake_astream_events(input: dict, config: dict, **kwargs: object):
        received_inputs.append(input)
        yield {"event": "on_chain_start", "name": "analyze_images", "run_id": "run-1"}
        yield {"event": "on_chain_end", "name": "analyze_images", "run_id": "run-1"}
        return
        yield  # pragma: no cover - makes this an async generator

    graph.astream_events = fake_astream_events  # type: ignore[method-assign]
    graph.aget_state = _fake_aget_state("Two cats.")  # type: ignore[method-assign]
    settings = build_test_settings()
    open_thinking_stream = FakeOpenThinkingStream()
    set_status = FakeSetStatus()
    download_image = FakeDownloadImage({"F1": b"\x89PNG"})  # F2 has no configured result
    in_flight: InFlightTurns = {}
    event = {
        "user": "U_ALLOWED",
        "channel": "D1",
        "ts": "111.1",
        "text": "what are these?",
        "files": [
            {"id": "F1", "mimetype": "image/png", "url_private": "https://x/1.png"},
            {"id": "F2", "mimetype": "image/png", "url_private": "https://x/2.png"},
        ],
    }

    await handle_slack_message(
        event,
        graph=graph,
        settings=settings,
        set_status=set_status,
        open_thinking_stream=open_thinking_stream,
        download_image=download_image,
        in_flight=in_flight,
    )

    sent_content = received_inputs[0]["messages"][0].content
    assert sent_content[0] == {
        "type": "text",
        "text": "what are these?\n\n[另有 1 張圖片無法讀取]",
    }
    assert len([b for b in sent_content if b.get("type") == "image_url"]) == 1


async def test_an_image_task_card_and_a_tool_task_card_stack_in_the_same_turn() -> None:
    graph = build_test_graph([AIMessage(content="unused")])

    async def fake_astream_events(input: dict, config: dict, **kwargs: object):
        yield {"event": "on_chain_start", "name": "analyze_images", "run_id": "run-1"}
        yield {"event": "on_chain_end", "name": "analyze_images", "run_id": "run-1"}
        yield {"event": "on_tool_start", "name": "web_search", "run_id": "run-2"}
        yield {
            "event": "on_tool_end",
            "name": "web_search",
            "run_id": "run-2",
            "data": {
                "output": ToolMessage(
                    content="Weather: Sunny (http://example.com)",
                    name="web_search",
                    tool_call_id="run-2",
                    artifact=[{"title": "Weather", "url": "http://example.com"}],
                )
            },
        }

    graph.astream_events = fake_astream_events  # type: ignore[method-assign]
    graph.aget_state = _fake_aget_state("It's sunny in the photo's location.")  # type: ignore[method-assign]
    settings = build_test_settings()
    open_thinking_stream = FakeOpenThinkingStream()
    set_status = FakeSetStatus()
    download_image = FakeDownloadImage({"F1": b"\x89PNG"})
    in_flight: InFlightTurns = {}
    event = {
        "user": "U_ALLOWED",
        "channel": "D1",
        "ts": "111.1",
        "text": "where is this and what's the weather?",
        "files": [{"id": "F1", "mimetype": "image/png", "url_private": "https://x/1.png"}],
    }

    await handle_slack_message(
        event,
        graph=graph,
        settings=settings,
        set_status=set_status,
        open_thinking_stream=open_thinking_stream,
        download_image=download_image,
        in_flight=in_flight,
    )

    (stream,) = open_thinking_stream.streams
    task_ids_in_order = [u[0] for u in stream.updates]
    assert task_ids_in_order == ["run-1", "run-1", "run-2", "run-2"]


async def test_open_thinking_stream_posts_no_placeholder_content_up_front() -> None:
    """Guards against a real bug: stream text is cumulative, so an initial placeholder
    would stay stuck in front of everything appended afterward (see ADR 0004)."""
    from slack_app import _make_open_thinking_stream

    class FakeRawChatStream:
        def __init__(self) -> None:
            self.append_calls: list[dict] = []

        async def append(self, **kwargs: object) -> None:
            self.append_calls.append(kwargs)

        async def stop(self, **kwargs: object) -> None:
            pass

    class FakeClient:
        def __init__(self) -> None:
            self.chat_stream_calls: list[dict] = []
            self.raw = FakeRawChatStream()

        async def chat_stream(self, **kwargs: object) -> FakeRawChatStream:
            self.chat_stream_calls.append(kwargs)
            return self.raw

    class FakeApp:
        def __init__(self) -> None:
            self.client = FakeClient()

    app = FakeApp()
    settings = build_test_settings()

    open_thinking_stream = _make_open_thinking_stream(app, settings)  # type: ignore[arg-type]
    await open_thinking_stream("D1", "111.1")

    assert app.client.chat_stream_calls == [
        {
            "channel": "D1",
            "thread_ts": "111.1",
            "task_display_mode": "timeline",
            "recipient_user_id": "U_ALLOWED",
        }
    ]
    assert app.client.raw.append_calls == []

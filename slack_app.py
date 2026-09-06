"""Slack DM Interface: Socket Mode wiring plus the handle_slack_message seam."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import cast

import aiohttp
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph.state import CompiledStateGraph
from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg_pool import AsyncConnectionPool
from slack_bolt.adapter.socket_mode.aiohttp import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp

from config import Settings, load_settings
from execution_traces import (
    BoundedBatchExporter,
    OtlpHttpExporter,
    PersistentAlertPolicy,
    TelemetrySpan,
    TraceRecorder,
)
from llm import build_llm
from long_term_memory import DEFAULT_POOL_KWARGS, build_async_store
from main import SENT_AT_KEY, USER_ID, build_graph, current_sent_at
from skills import load_skills
from slack_images import MessageContent, build_image_content, extract_image_files
from slack_status import (
    build_output_summary,
    build_sources,
    build_web_search_output,
    tool_done_label,
    tool_live_label,
)
from slack_stream import OpenThinkingStream, SlackThinkingStream, ThinkingStream, bounded_final_text
from tools import make_tools

logger = logging.getLogger(__name__)

ERROR_REPLY = "發生錯誤，請稍後再試"
EMPTY_REPLY = "模型沒有產生回覆內容，請再試一次"
STATUS_THINKING = "思考中…"
STATUS_RESUMED = "已收到你的補充，重新整理回覆中…"

# The processing step that turns image content into text (see CONTEXT.md: Thinking
# Step, ADR 0006) -- not a Tool, so it needs its own on_chain_start/on_chain_end check.
IMAGE_ANALYSIS_NODE = "analyze_images"

SetStatus = Callable[[str, str, str], Awaitable[None]]
DownloadImage = Callable[[dict], Awaitable[bytes]]
# Per-thread_id registry of the Turn (see CONTEXT.md) currently being processed,
# so a follow-up message in the same conversation can cancel and restart it.
InFlightTurns = dict[str, "asyncio.Task"]


async def _build_turn_content(event: dict, download_image: DownloadImage) -> MessageContent:
    text: str = event.get("text", "")
    image_files = extract_image_files(event.get("files", []))
    if not image_files:
        return text

    images: list[tuple[bytes, str]] = []
    failed = 0
    for file in image_files:
        try:
            images.append((await download_image(file), file.get("mimetype", "image/png")))
        except Exception:
            logger.exception("Failed to download Slack image file %s", file.get("id"))
            failed += 1

    if not images:
        return _append_note(text, f"{failed} 張圖片無法讀取")
    if failed:
        text = _append_note(text, f"另有 {failed} 張圖片無法讀取")
    return build_image_content(text, images)


def _append_note(text: str, note: str) -> str:
    return f"{text}\n\n[{note}]" if text else f"[{note}]"


async def _run_turn(
    graph: CompiledStateGraph,
    config: RunnableConfig,
    content: MessageContent,
    stream: ThinkingStream,
    trace_recorder: TraceRecorder | None = None,
    trace: TelemetrySpan | None = None,
) -> str:
    # Stream (rather than a single ainvoke) so each Thinking Step (see CONTEXT.md) -- a
    # tool call or the mandatory image-analysis step starting or finishing -- can be
    # reflected as a Task Card update live.
    human_message = HumanMessage(
        content=content, additional_kwargs={SENT_AT_KEY: current_sent_at()}
    )
    tool_inputs: dict[str, object] = {}
    async for event in graph.astream_events({"messages": [human_message]}, config, version="v2"):
        name = event.get("name")
        run_id = str(event.get("run_id") or "")
        parent_ids = event.get("parent_ids") or []
        parent_step_id = str(parent_ids[-1]) if parent_ids else None
        event_type = event["event"]
        if trace_recorder is not None and trace is not None and run_id:
            # The public graph event stream is the capture boundary; it deliberately
            # avoids changing graph callbacks or Slack Thinking Step behavior.
            if event_type in {"on_chat_model_start", "on_tool_start", "on_chain_start"}:
                category = (
                    "model"
                    if event_type == "on_chat_model_start"
                    else "tool"
                    if event_type == "on_tool_start"
                    else "image_analysis"
                    if name == IMAGE_ANALYSIS_NODE
                    else "graph_node"
                )
                await _record_telemetry(
                    trace_recorder.start_step(
                        trace,
                        run_id,
                        parent_step_id,
                        category,
                        event.get("data", {}).get("input"),
                        {"name": str(name)},
                    )
                )
            elif event_type in {"on_chat_model_end", "on_tool_end", "on_chain_end"}:
                await _record_telemetry(
                    trace_recorder.finish_step(
                        trace, run_id, "completed", output=event.get("data", {}).get("output")
                    )
                )
            elif event_type.endswith("_error"):
                await _record_telemetry(
                    trace_recorder.finish_step(
                        trace, run_id, "failed", error=event.get("data", {}).get("error")
                    )
                )
        if event_type == "on_tool_start":
            tool_input = event.get("data", {}).get("input")
            if run_id:
                tool_inputs[run_id] = tool_input
            await stream.update_task(run_id, tool_live_label(name, tool_input), "in_progress")
        elif event_type == "on_tool_end":
            tool_input = tool_inputs.pop(run_id, None) if run_id else None
            output_message = event["data"]["output"]
            content_str = str(getattr(output_message, "content", output_message))
            artifact = getattr(output_message, "artifact", None) or []
            if name == "web_search":
                output = build_web_search_output(artifact)
                sources = build_sources(artifact)
            else:
                output = build_output_summary(name, content_str)
                sources = None
            await stream.update_task(
                run_id, tool_done_label(name, tool_input), "complete", output, sources
            )
        elif event_type == "on_chain_start" and name == IMAGE_ANALYSIS_NODE:
            # Assumes on_chain_start/on_chain_end share a run_id per node execution, the
            # way on_tool_start/on_tool_end already do -- unverified beyond scripted
            # tests; confirm with a real-model probe if this Task Card looks wrong.
            await stream.update_task(run_id, tool_live_label(name), "in_progress")
        elif event_type == "on_chain_end" and name == IMAGE_ANALYSIS_NODE:
            await stream.update_task(run_id, tool_done_label(name), "complete")
    state = await graph.aget_state(config)
    reply = state.values["messages"][-1].content
    # Slack rejects an empty text (no_text error); some models can finish with empty content.
    await stream.finish(reply or EMPTY_REPLY)
    return str(reply or EMPTY_REPLY)


async def _record_telemetry(operation: Awaitable[None]) -> None:
    """Keep telemetry capture failures outside Slack Turn control flow."""
    try:
        await operation
    except Exception:
        logger.exception("Telemetry capture failed")


async def _finish_stream(stream: ThinkingStream, markdown_text: str, *, reason: str) -> None:
    """Best-effort Slack stream cleanup that never changes a Turn's outcome."""
    try:
        await stream.finish(markdown_text)
    except Exception:
        # Slack may return a transient 5xx even if it has already stopped the stream.
        # In particular, a failed cleanup of a cancelled Turn must not prevent its
        # replacement Turn from being created.
        logger.exception("Failed to finish Slack stream while %s", reason)


async def handle_slack_message(
    event: dict,
    *,
    graph: CompiledStateGraph,
    settings: Settings,
    set_status: SetStatus,
    open_thinking_stream: OpenThinkingStream,
    download_image: DownloadImage,
    in_flight: InFlightTurns,
    trace_recorder: TraceRecorder | None = None,
) -> None:
    if event.get("user") != settings.slack_allowed_user_id:
        return

    channel = event["channel"]
    thread_id = event.get("thread_ts") or event["ts"]
    config: RunnableConfig = {"configurable": {"thread_id": thread_id, "user_id": USER_ID}}
    content = await _build_turn_content(event, download_image)

    previous_turn = in_flight.get(thread_id)
    if previous_turn is not None and not previous_turn.done():
        previous_turn.cancel()
        with suppress(asyncio.CancelledError):
            await previous_turn
        await set_status(channel, thread_id, STATUS_RESUMED)
    else:
        await set_status(channel, thread_id, STATUS_THINKING)

    # No initial content here: chat.appendStream/stopStream text is cumulative, so any
    # placeholder posted at open time would stay stuck in front of everything that
    # follows (see ADR 0004). set_status above is Slack's own "is thinking" affordance.
    stream = await open_thinking_stream(channel, thread_id)
    trace = None
    if trace_recorder is not None:
        try:
            trace = await trace_recorder.start_trace(thread_id, event.get("text", ""))
        except Exception:
            logger.exception("Telemetry trace start failed")

    async def _turn() -> None:
        try:
            reply = await _run_turn(graph, config, content, stream, trace_recorder, trace)
            if trace_recorder is not None and trace is not None:
                _, original_length, truncated = bounded_final_text(reply)
                await _record_telemetry(
                    trace_recorder.finish_trace(
                        trace,
                        "completed",
                        output=reply,
                        attributes={
                            "turn.output.length": original_length,
                            "turn.output.truncated": truncated,
                        },
                    )
                )
        except asyncio.CancelledError:
            await _finish_stream(stream, STATUS_RESUMED, reason="cancelling a superseded turn")
            if trace_recorder is not None and trace is not None:
                await _record_telemetry(trace_recorder.finish_trace(trace, "cancelled"))
            raise
        except Exception as exc:
            logger.exception(
                "handle_slack_message failed for channel=%s thread_id=%s", channel, thread_id
            )
            await _finish_stream(stream, ERROR_REPLY, reason="reporting a turn failure")
            if trace_recorder is not None and trace is not None:
                await _record_telemetry(
                    trace_recorder.finish_trace(trace, "failed", error=repr(exc))
                )

    turn = asyncio.ensure_future(_turn())
    in_flight[thread_id] = turn

    try:
        await turn
    except asyncio.CancelledError:
        return
    finally:
        if in_flight.get(thread_id) is turn:
            del in_flight[thread_id]


def _make_open_thinking_stream(app: AsyncApp, settings: Settings) -> OpenThinkingStream:
    async def open_thinking_stream(channel: str, thread_id: str) -> ThinkingStream:
        raw = await app.client.chat_stream(
            channel=channel,
            thread_ts=thread_id,
            task_display_mode="timeline",
            recipient_user_id=settings.slack_allowed_user_id,
        )
        return SlackThinkingStream(raw)

    return open_thinking_stream


def _build_app(
    graph: CompiledStateGraph, settings: Settings, trace_recorder: TraceRecorder
) -> AsyncApp:
    app = AsyncApp(token=settings.slack_bot_token)
    if settings.observability_maintenance_channel:

        async def notify_maintenance(message: str) -> None:
            await app.client.chat_postMessage(
                channel=settings.observability_maintenance_channel, text=message
            )

        trace_recorder.exporter.alert_policy = PersistentAlertPolicy(notify_maintenance)
    in_flight: InFlightTurns = {}
    open_thinking_stream = _make_open_thinking_stream(app, settings)

    async def download_image(file: dict) -> bytes:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                file["url_private"],
                headers={"Authorization": f"Bearer {settings.slack_bot_token}"},
            ) as response:
                response.raise_for_status()
                return await response.read()

    @app.event("message")
    async def _on_message(event: dict) -> None:
        if event.get("channel_type") != "im":
            return

        async def set_status(channel: str, thread_ts: str, status: str) -> None:
            await app.client.assistant_threads_setStatus(
                channel_id=channel, thread_ts=thread_ts, status=status, loading_messages=[status]
            )

        await handle_slack_message(
            event,
            graph=graph,
            settings=settings,
            set_status=set_status,
            open_thinking_stream=open_thinking_stream,
            download_image=download_image,
            in_flight=in_flight,
            trace_recorder=trace_recorder,
        )

    return app


async def _run(
    graph: CompiledStateGraph, settings: Settings, trace_recorder: TraceRecorder
) -> None:
    app = _build_app(graph, settings, trace_recorder)
    handler = AsyncSocketModeHandler(app, settings.slack_app_token)
    try:
        await handler.start_async()
    finally:
        await trace_recorder.exporter.flush()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = load_settings()
    llm = build_llm(settings)
    skills = load_skills(settings.skills_dir)
    tools = make_tools(settings, skills)

    async def _amain() -> None:
        async with cast(
            AsyncConnectionPool[AsyncConnection[DictRow]],
            AsyncConnectionPool(
                settings.database_url,
                kwargs=DEFAULT_POOL_KWARGS,
            ),
        ) as pool:
            checkpointer = AsyncPostgresSaver(pool)
            await checkpointer.setup()
            store = await build_async_store(pool, settings)
            graph = build_graph(llm, tools, checkpointer, store, skills)
            trace_recorder = TraceRecorder(
                BoundedBatchExporter(
                    OtlpHttpExporter(settings.observability_otlp_endpoint),
                    max_queue_size=settings.observability_queue_size,
                )
            )
            await _run(graph, settings, trace_recorder)

    asyncio.run(_amain())


if __name__ == "__main__":
    main()

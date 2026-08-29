"""Slack DM Interface: Socket Mode wiring plus the handle_slack_message seam."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph.state import CompiledStateGraph
from slack_bolt.adapter.socket_mode.aiohttp import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp

from config import Settings, load_settings
from llm import build_llm
from main import USER_ID, build_graph, open_store
from tools import make_tools

logger = logging.getLogger(__name__)

ERROR_REPLY = "發生錯誤，請稍後再試"
EMPTY_REPLY = "模型沒有產生回覆內容，請再試一次"
STATUS_THINKING = "思考中…"
STATUS_RESUMED = "已收到你的補充，重新整理回覆中…"

PostReply = Callable[[str, str, str], Awaitable[None]]
SetStatus = Callable[[str, str, str], Awaitable[None]]
# Per-thread_id registry of the Turn (see CONTEXT.md) currently being processed,
# so a follow-up message in the same conversation can cancel and restart it.
InFlightTurns = dict[str, "asyncio.Task"]


async def handle_slack_message(
    event: dict,
    *,
    graph: CompiledStateGraph,
    settings: Settings,
    post_reply: PostReply,
    set_status: SetStatus,
    in_flight: InFlightTurns,
) -> None:
    if event.get("user") != settings.slack_allowed_user_id:
        return

    channel = event["channel"]
    thread_id = event.get("thread_ts") or event["ts"]
    config: RunnableConfig = {"configurable": {"thread_id": thread_id, "user_id": USER_ID}}

    previous_turn = in_flight.get(thread_id)
    if previous_turn is not None and not previous_turn.done():
        previous_turn.cancel()
        with suppress(asyncio.CancelledError):
            await previous_turn
        await set_status(channel, thread_id, STATUS_RESUMED)
    else:
        await set_status(channel, thread_id, STATUS_THINKING)

    turn = asyncio.ensure_future(
        graph.ainvoke({"messages": [HumanMessage(content=event["text"])]}, config)
    )
    in_flight[thread_id] = turn

    try:
        result = await turn
    except asyncio.CancelledError:
        return
    except Exception:
        logger.exception(
            "handle_slack_message failed for channel=%s thread_id=%s", channel, thread_id
        )
        await post_reply(channel, thread_id, ERROR_REPLY)
        return
    finally:
        if in_flight.get(thread_id) is turn:
            del in_flight[thread_id]

    reply = result["messages"][-1].content
    # Slack rejects an empty text (no_text error); some models can finish with empty content.
    await post_reply(channel, thread_id, reply or EMPTY_REPLY)


def _build_app(graph: CompiledStateGraph, settings: Settings) -> AsyncApp:
    app = AsyncApp(token=settings.slack_bot_token)
    in_flight: InFlightTurns = {}

    @app.event("message")
    async def _on_message(event: dict, say: Callable[..., Awaitable[object]]) -> None:
        if event.get("channel_type") != "im":
            return

        async def post_reply(channel: str, thread_ts: str, text: str) -> None:
            await say(channel=channel, thread_ts=thread_ts, text=text)

        async def set_status(channel: str, thread_ts: str, status: str) -> None:
            await app.client.assistant_threads_setStatus(
                channel_id=channel, thread_ts=thread_ts, status=status
            )

        await handle_slack_message(
            event,
            graph=graph,
            settings=settings,
            post_reply=post_reply,
            set_status=set_status,
            in_flight=in_flight,
        )

    return app


async def _run(graph: CompiledStateGraph, settings: Settings) -> None:
    app = _build_app(graph, settings)
    handler = AsyncSocketModeHandler(app, settings.slack_app_token)
    await handler.start_async()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = load_settings()
    checkpoint_path = Path(settings.checkpoint_db_path)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    llm = build_llm(settings)
    tools = make_tools(settings)

    async def _amain() -> None:
        async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
            with open_store(settings) as store:
                graph = build_graph(llm, tools, checkpointer, store)
                await _run(graph, settings)

    asyncio.run(_amain())


if __name__ == "__main__":
    main()

"""Slack DM Interface: Socket Mode wiring plus the handle_slack_message seam."""

import logging
from collections.abc import Callable
from pathlib import Path

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph.state import CompiledStateGraph
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from config import Settings, load_settings
from llm import build_llm
from main import USER_ID, build_graph, open_store
from tools import make_tools

logger = logging.getLogger(__name__)

ERROR_REPLY = "發生錯誤，請稍後再試"

PostReply = Callable[[str, str, str], None]


def handle_slack_message(
    event: dict,
    *,
    graph: CompiledStateGraph,
    settings: Settings,
    post_reply: PostReply,
) -> None:
    if event.get("user") != settings.slack_allowed_user_id:
        return

    channel = event["channel"]
    thread_id = event.get("thread_ts") or event["ts"]
    config: RunnableConfig = {"configurable": {"thread_id": thread_id, "user_id": USER_ID}}

    try:
        result = graph.invoke({"messages": [HumanMessage(content=event["text"])]}, config)
        reply = result["messages"][-1].content
    except Exception:
        logger.exception(
            "handle_slack_message failed for channel=%s thread_id=%s", channel, thread_id
        )
        reply = ERROR_REPLY

    post_reply(channel, thread_id, reply)


def _build_app(graph: CompiledStateGraph, settings: Settings) -> App:
    app = App(token=settings.slack_bot_token)

    @app.event("message")
    def _on_message(event: dict, say: Callable[..., None]) -> None:
        if event.get("channel_type") != "im":
            return

        def post_reply(channel: str, thread_ts: str, text: str) -> None:
            say(channel=channel, thread_ts=thread_ts, text=text)

        handle_slack_message(event, graph=graph, settings=settings, post_reply=post_reply)

    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = load_settings()
    checkpoint_path = Path(settings.checkpoint_db_path)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    llm = build_llm(settings)
    tools = make_tools(settings)

    with (
        SqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer,
        open_store(settings) as store,
    ):
        graph = build_graph(llm, tools, checkpointer, store)
        app = _build_app(graph, settings)
        SocketModeHandler(app, settings.slack_app_token).start()


if __name__ == "__main__":
    main()

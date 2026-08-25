"""Graph assembly and CLI entrypoint for the Assistant."""

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import StateGraph
from langgraph.graph.message import MessagesState
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.store.base import BaseStore

from config import load_settings
from llm import build_llm
from long_term_memory import build_store
from stats import LoggingCallbackHandler, summarize_call_stats, summarize_tool_stats
from tools import make_tools

# This assistant is designed for a single person; there is no multi-user concept.
USER_ID = "the-user"

SYSTEM_PROMPT = (
    "You are a personal AI assistant. You may call the recall_memory tool if "
    "relevant saved facts would help you answer, and the save_memory tool when "
    "you judge a fact is worth remembering long-term. You may call the "
    "web_search tool when you need current or unknown information. Only call a "
    "tool when it genuinely helps the current turn; never take action the user "
    "didn't ask for."
)


def call_model(llm_with_tools: Any) -> Callable[[MessagesState, RunnableConfig], MessagesState]:
    def _call_model(state: MessagesState, config: RunnableConfig) -> MessagesState:
        messages = [SystemMessage(content=SYSTEM_PROMPT), *state["messages"]]
        response = llm_with_tools.invoke(messages, config)
        return {"messages": [response]}

    return _call_model


def build_graph(
    llm: BaseChatModel,
    tools: list[BaseTool],
    checkpointer: BaseCheckpointSaver,
    store: BaseStore,
) -> Any:
    llm_with_tools = llm.bind_tools(tools)
    graph = StateGraph(MessagesState)
    graph.add_node("model", call_model(llm_with_tools))  # type: ignore[call-overload]
    graph.add_node("tools", ToolNode(tools))
    graph.set_entry_point("model")
    graph.add_conditional_edges("model", tools_condition)
    graph.add_edge("tools", "model")
    return graph.compile(checkpointer=checkpointer, store=store)


@contextmanager
def open_store(settings: Any) -> Iterator[BaseStore]:
    conn = sqlite3.connect("data/memory_store.sqlite", check_same_thread=False)
    try:
        yield build_store(conn, settings)
    finally:
        conn.close()


def main() -> None:
    Path("data").mkdir(parents=True, exist_ok=True)
    settings = load_settings()
    llm = build_llm(settings)
    tools = make_tools(settings)
    handler = LoggingCallbackHandler()

    with (
        SqliteSaver.from_conn_string("data/checkpoints.sqlite") as checkpointer,
        open_store(settings) as store,
    ):
        app = build_graph(llm, tools, checkpointer, store)
        config: RunnableConfig = {
            "configurable": {"thread_id": "assistant", "user_id": USER_ID},
            "callbacks": [handler],
        }
        print("Personal AI Assistant (type 'exit' or 'quit' to stop)")
        while True:
            try:
                user_input = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if user_input.lower() in {"exit", "quit"}:
                break
            if not user_input:
                continue
            result = app.invoke({"messages": [HumanMessage(content=user_input)]}, config)
            reply = result["messages"][-1]
            print(f"Assistant: {reply.content}")

    print("\n--- Run stats ---")
    print("LLM calls:", summarize_call_stats(handler.call_stats))
    print("Tool calls:", summarize_tool_stats(handler.tool_stats))


if __name__ == "__main__":
    main()

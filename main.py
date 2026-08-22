import argparse
import time
from typing import TypedDict
from uuid import uuid4

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.store.base import BaseStore
from langgraph.store.sqlite import SqliteStore
from pydantic import BaseModel, Field

from checkpoint_history import CHECKPOINT_DB_PATH
from leaked_tool_call import LEAKED_TOOL_CALL_PATTERN, parse_leaked_tool_call
from llm import build_llm
from long_term_memory import MEMORY_DB_PATH, memory_index_config
from stats import (
    CallStat,
    LoggingCallbackHandler,
    ToolStat,
    summarize_call_stats,
    summarize_tool_stats,
)
from tools import add_numbers, get_current_time, recall_memory, save_memory

SYSTEM_PROMPT = (
    "You are an AI assistant with tool-calling capabilities. When you need to use a tool, output ONLY the valid JSON tool call format specified by the API. DO NOT output any XML tags, do not use '<|think|>' tags, and do not wrap your response in markdown prose explaining the tool call."
    "請一律使用繁體中文回答，不要夾雜其他語言。"
    "若需要知道使用者過去提過的偏好或事實，可呼叫 recall_memory 工具查詢，不要憑空假設。"
)


class AgentResponse(BaseModel):
    """Structured final answer returned to the caller."""

    answer: str = Field(
        ..., description="The final answer to the user's request, in Traditional Chinese."
    )
    used_tools: list[str] = Field(
        default_factory=list, description="Names of tools invoked while answering."
    )


class AgentContext(TypedDict):
    user_id: str


def build_agent_context(user_id: str) -> AgentContext:
    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("user_id must be a non-empty string")
    return {"user_id": user_id}


def build_graph(
    llm: BaseChatModel, checkpointer: BaseCheckpointSaver, store: BaseStore
) -> CompiledStateGraph[MessagesState, AgentContext, MessagesState, MessagesState]:
    tools = [get_current_time, add_numbers, save_memory, recall_memory]
    llm_with_tools = llm.bind_tools(tools)

    def call_model(state: MessagesState) -> MessagesState:
        messages = state["messages"]
        response = llm_with_tools.invoke(messages, config={"metadata": {"call_kind": "chat"}})

        content = response.content if isinstance(response.content, str) else ""
        if not response.tool_calls and LEAKED_TOOL_CALL_PATTERN.search(content):
            leaked = parse_leaked_tool_call(content)
            if leaked:
                response = AIMessage(
                    content="",
                    tool_calls=[{**leaked, "id": str(uuid4())}],
                )
        return {"messages": [response]}

    graph = StateGraph(MessagesState, context_schema=AgentContext)
    graph.add_node("model", call_model)
    graph.add_node("tools", ToolNode(tools))

    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", tools_condition, {"tools": "tools", END: END})
    graph.add_edge("tools", "model")
    # Checkpointer keeps message history per thread_id; store keeps facts per user_id across threads.
    return graph.compile(checkpointer=checkpointer, store=store)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LangGraph practice agent CLI")
    parser.add_argument("--user-id", default="demo-user", help="Namespace for long-term memory")
    parser.add_argument(
        "--thread-id", default="demo-thread", help="Checkpointed conversation thread"
    )
    return parser.parse_args()


def main():
    args = parse_args()
    llm = build_llm()

    # Run-level stats accumulate across every Turn for the final summary.
    run_call_stats: list[CallStat] = []
    run_tool_stats: list[ToolStat] = []
    run_seconds = 0.0
    turn_count = 0

    # SqliteSaver persists per-thread history; SqliteStore persists per-user long-term memories.
    with (
        SqliteSaver.from_conn_string(CHECKPOINT_DB_PATH) as checkpointer,
        SqliteStore.from_conn_string(MEMORY_DB_PATH, index=memory_index_config()) as store,
    ):
        app = build_graph(llm, checkpointer, store)
        base_config: RunnableConfig = {
            "configurable": {"thread_id": args.thread_id}
        }
        context = build_agent_context(args.user_id)

        print(f"=== thread={args.thread_id} user={args.user_id} (輸入 exit/quit 結束對話) ===")
        while True:
            try:
                user_input = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if not user_input:
                continue
            if user_input.lower() in {"exit", "quit"}:
                break

            handler = LoggingCallbackHandler()
            config: RunnableConfig = {**base_config, "callbacks": [handler]}

            # Check persisted checkpoint state instead of an in-process set, so reruns
            # against an existing checkpoint DB don't re-inject a duplicate system prompt.
            is_new_thread = not app.get_state(config).values.get("messages")

            messages: list[AnyMessage] = []
            if is_new_thread:
                messages.append(SystemMessage(content=SYSTEM_PROMPT))
            messages.append(HumanMessage(content=user_input))

            # Single stopwatch around the whole Turn; call_stats/tool_stats are a breakdown,
            # not addends, since tool time can itself contain nested LLM time (e.g. memory merge).
            turn_started_at = time.monotonic()
            result = app.invoke({"messages": messages}, config=config, context=context)
            turn_seconds = time.monotonic() - turn_started_at

            used_tools = [
                message.name
                for message in result["messages"]
                if isinstance(message, ToolMessage) and message.name
            ]
            last_message = result["messages"][-1]
            answer = last_message.content if isinstance(last_message, AIMessage) else ""
            structured = AgentResponse(answer=str(answer), used_tools=used_tools)

            print(f"Agent: {structured.answer}")
            if structured.used_tools:
                print(f"Tools used: {structured.used_tools}")
            print(
                f"[stats] turn: {turn_seconds:.2f}s total | "
                f"{summarize_call_stats(handler.call_stats)} | "
                f"{summarize_tool_stats(handler.tool_stats)}\n"
            )

            run_call_stats.extend(handler.call_stats)
            run_tool_stats.extend(handler.tool_stats)
            run_seconds += turn_seconds
            turn_count += 1

        if turn_count:
            print("=== Run totals ===")
            print(
                f"[stats] run: {run_seconds:.2f}s total across {turn_count} turns | "
                f"{summarize_call_stats(run_call_stats)} | "
                f"{summarize_tool_stats(run_tool_stats)}"
            )


if __name__ == "__main__":
    main()

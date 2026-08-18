"""Run a fixed set of demo conversations and print the full interaction log.

Restores the old `main.py` behavior (before it became an interactive CLI, see TODO.md #5)
for quickly eyeballing model/tool-call behavior locally. Needs a real LM Studio server.
"""

import time
from pathlib import Path

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.store.sqlite import SqliteStore

from checkpoint_history import CHECKPOINT_DB_PATH
from long_term_memory import MEMORY_DB_PATH, memory_index_config
from main import (
    SYSTEM_PROMPT,
    AgentResponse,
    CallStat,
    LoggingCallbackHandler,
    ToolStat,
    _summarize_call_stats,
    _summarize_tool_stats,
    build_graph,
    build_llm,
)

DEMO_USER_ID = "demo-user"
DEMO_CONVERSATIONS = [
    ("demo-thread", "現在幾點？順便幫我算 23 + 19"),
    ("demo-thread", "我剛剛請你算的兩個數字加起來是多少？"),
    ("demo-thread", "我喜歡喝黑咖啡，不加糖，麻煩你記住這個偏好。"),
    ("demo-thread-2", "你知道我喜歡喝什麼咖啡嗎？"),
    ("demo-thread-3", "你知道我叫什麼名字嗎？"),
]


def _cleanup_data_files() -> None:
    """Remove demo-run sqlite files (and their -wal/-shm sidecars) under data/."""
    for db_path in (CHECKPOINT_DB_PATH, MEMORY_DB_PATH):
        for suffix in ("", "-wal", "-shm"):
            Path(f"{db_path}{suffix}").unlink(missing_ok=True)


def main() -> None:
    llm = build_llm()

    # Run-level stats accumulate across every Turn (demo conversation) for the final summary.
    run_call_stats: list[CallStat] = []
    run_tool_stats: list[ToolStat] = []
    run_seconds = 0.0

    try:
        # SqliteSaver persists per-thread history; SqliteStore persists per-user long-term memories.
        with (
            SqliteSaver.from_conn_string(CHECKPOINT_DB_PATH) as checkpointer,
            SqliteStore.from_conn_string(MEMORY_DB_PATH, index=memory_index_config()) as store,
        ):
            app = build_graph(llm, checkpointer, store)

            for idx, (thread_id, user_input) in enumerate(DEMO_CONVERSATIONS, start=1):
                print(f"=== Case {idx} (thread={thread_id}) ===")
                print(f"User: {user_input}\n")

                handler = LoggingCallbackHandler()
                config: RunnableConfig = {
                    "configurable": {"thread_id": thread_id, "user_id": DEMO_USER_ID},
                    "callbacks": [handler],
                }

                # Check persisted checkpoint state instead of an in-process set, so reruns
                # against an existing checkpoint DB don't re-inject a duplicate system prompt.
                is_new_thread = not app.get_state(config).values.get("messages")

                messages: list[BaseMessage] = []
                if is_new_thread:
                    messages.append(SystemMessage(content=SYSTEM_PROMPT))
                messages.append(HumanMessage(content=user_input))

                turn_started_at = time.monotonic()
                result = app.invoke({"messages": messages}, config=config)
                turn_seconds = time.monotonic() - turn_started_at

                used_tools = [
                    message.name
                    for message in result["messages"]
                    if isinstance(message, ToolMessage) and message.name
                ]
                last_message = result["messages"][-1]
                answer = last_message.content if isinstance(last_message, AIMessage) else ""
                structured = AgentResponse(answer=str(answer), used_tools=used_tools)

                print(f"Tools used: {structured.used_tools}")
                print(f"Answer: {structured.answer}")
                print(
                    f"[stats] turn: {turn_seconds:.2f}s total | "
                    f"{_summarize_call_stats(handler.call_stats)} | "
                    f"{_summarize_tool_stats(handler.tool_stats)}\n"
                )

                run_call_stats.extend(handler.call_stats)
                run_tool_stats.extend(handler.tool_stats)
                run_seconds += turn_seconds

            print("=== Run totals ===")
            print(
                f"[stats] run: {run_seconds:.2f}s total across {len(DEMO_CONVERSATIONS)} turns | "
                f"{_summarize_call_stats(run_call_stats)} | "
                f"{_summarize_tool_stats(run_tool_stats)}"
            )
    finally:
        # Clean up the demo's sqlite files once connections are closed, so each `poe demo`
        # run starts fresh instead of accumulating state in data/.
        _cleanup_data_files()


if __name__ == "__main__":
    main()

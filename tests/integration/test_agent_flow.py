"""End-to-end checks against a real LM Studio server (see tests/integration/conftest.py).

Migrated from the demo conversations that used to be main.py's hardcoded test_cases (Q10):
tool-call path assertions are strict, cross-thread memory-recall *content* assertions are
loose (Q8) since a local small model's instruction-following isn't perfectly reliable.
"""

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.store.sqlite import SqliteStore

import main
from llm import build_llm
from long_term_memory import memory_index_config

pytestmark = pytest.mark.integration


def _send(app, config, *, is_new_thread: bool, user_input: str) -> dict:
    messages = []
    if is_new_thread:
        messages.append(SystemMessage(content=main.SYSTEM_PROMPT))
    messages.append(HumanMessage(content=user_input))
    return app.invoke({"messages": messages}, config=config)


def _used_tools(result: dict) -> list[str]:
    return [m.name for m in result["messages"] if isinstance(m, ToolMessage) and m.name]


def test_agent_uses_add_numbers_tool_for_an_arithmetic_request():
    llm = build_llm()
    with (
        SqliteSaver.from_conn_string(":memory:") as checkpointer,
        SqliteStore.from_conn_string(":memory:", index=memory_index_config()) as store,
    ):
        app = main.build_graph(llm, checkpointer, store)
        config = {"configurable": {"thread_id": "it-arithmetic", "user_id": "it-user"}}

        result = _send(app, config, is_new_thread=True, user_input="現在幾點？順便幫我算 23 + 19")

        assert "add_numbers" in _used_tools(result)
        final = result["messages"][-1]
        assert isinstance(final, AIMessage)
        assert final.content


def test_agent_uses_recall_memory_tool_for_an_unknown_fact():
    llm = build_llm()
    with (
        SqliteSaver.from_conn_string(":memory:") as checkpointer,
        SqliteStore.from_conn_string(":memory:", index=memory_index_config()) as store,
    ):
        app = main.build_graph(llm, checkpointer, store)
        config = {"configurable": {"thread_id": "it-unknown-fact", "user_id": "it-user-fresh"}}

        result = _send(app, config, is_new_thread=True, user_input="你知道我叫什麼名字嗎？")

        assert "recall_memory" in _used_tools(result)
        final = result["messages"][-1]
        assert isinstance(final, AIMessage)
        assert final.content


def test_agent_recalls_a_saved_preference_in_a_different_thread():
    llm = build_llm()
    user_id = "it-user-memory"
    with (
        SqliteSaver.from_conn_string(":memory:") as checkpointer,
        SqliteStore.from_conn_string(":memory:", index=memory_index_config()) as store,
    ):
        app = main.build_graph(llm, checkpointer, store)
        save_config = {"configurable": {"thread_id": "it-memory-save", "user_id": user_id}}
        recall_config = {"configurable": {"thread_id": "it-memory-recall", "user_id": user_id}}

        save_result = _send(
            app,
            save_config,
            is_new_thread=True,
            user_input="我喜歡喝黑咖啡，不加糖，麻煩你記住這個偏好。",
        )
        assert "save_memory" in _used_tools(save_result)

        recall_result = _send(
            app, recall_config, is_new_thread=True, user_input="你知道我喜歡喝什麼咖啡嗎？"
        )

        # Loose per Q8: only require the recall path was exercised and produced *some* answer,
        # not that the model correctly reproduces the saved wording.
        assert "recall_memory" in _used_tools(recall_result)
        final = recall_result["messages"][-1]
        assert isinstance(final, AIMessage)
        assert final.content

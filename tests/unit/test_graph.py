"""Primary seam: the graph's app.invoke() boundary (conversation + tool-call loop)."""

from conftest import build_test_graph, build_test_settings
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

import tools as tools_module
from long_term_memory import memory_namespace
from main import open_store


def test_assistant_replies_without_calling_any_tool() -> None:
    graph = build_test_graph([AIMessage(content="Hello! How can I help?")])
    config = {"configurable": {"thread_id": "t1", "user_id": "u1"}}

    result = graph.invoke({"messages": [HumanMessage(content="hi")]}, config)

    assert result["messages"][-1].content == "Hello! How can I help?"


def test_assistant_saves_a_memory_via_explicit_tool_call() -> None:
    tool_call = {"name": "save_memory", "args": {"content": "User likes tea"}, "id": "call-1"}
    responses = [
        AIMessage(content="", tool_calls=[tool_call]),
        AIMessage(content="Got it, I'll remember that."),
    ]
    graph = build_test_graph(responses)
    config = {"configurable": {"thread_id": "t1", "user_id": "u1"}}

    result = graph.invoke({"messages": [HumanMessage(content="I like tea")]}, config)

    assert result["messages"][-1].content == "Got it, I'll remember that."
    tool_message = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert "Saved memory" in tool_message.content


def test_assistant_recalls_a_memory_via_explicit_tool_call() -> None:
    tool_call = {"name": "recall_memory", "args": {"query": "tea"}, "id": "call-1"}
    responses = [
        AIMessage(content="", tool_calls=[tool_call]),
        AIMessage(content="You like tea."),
    ]
    graph = build_test_graph(responses)
    config = {"configurable": {"thread_id": "t1", "user_id": "u1"}}
    graph.store.put(("u1", "memories"), "existing", {"content": "User likes tea"})

    result = graph.invoke({"messages": [HumanMessage(content="what do I like?")]}, config)

    assert result["messages"][-1].content == "You like tea."
    tool_message = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert "User likes tea" in tool_message.content


def test_assistant_searches_the_web_via_explicit_tool_call(monkeypatch) -> None:
    class FakeDDGS:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def text(self, query: str, max_results: int = 5) -> list[dict[str, str]]:
            return [{"title": "Weather", "body": "Sunny all day", "href": "http://example.com"}]

    monkeypatch.setattr(tools_module, "DDGS", FakeDDGS)
    tool_call = {"name": "web_search", "args": {"query": "weather today"}, "id": "call-1"}
    responses = [
        AIMessage(content="", tool_calls=[tool_call]),
        AIMessage(content="It's sunny today."),
    ]
    graph = build_test_graph(responses)
    config = {"configurable": {"thread_id": "t2", "user_id": "u1"}}

    result = graph.invoke({"messages": [HumanMessage(content="What's the weather?")]}, config)

    assert result["messages"][-1].content == "It's sunny today."
    tool_message = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert "Sunny all day" in tool_message.content


def test_open_store_uses_autocommit_mode_for_sqlite_transactions() -> None:
    settings = build_test_settings()
    namespace = memory_namespace("tx-user")

    with open_store(settings) as store:
        store.put(namespace, "existing", {"content": "User likes tea"})
        matches = store.search(namespace, query="tea", limit=5)

    assert len(matches) == 1
    assert matches[0].value["content"] == "User likes tea"

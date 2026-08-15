"""Unit tests for build_graph's routing/retry logic: tools_condition wiring, the leaked
tool-call retry loop, and the best-effort leaked tool-call recovery."""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from tests.unit.conftest import build_test_graph, fake_llm

_CONFIG = {"configurable": {"thread_id": "t1", "user_id": "u1"}}


def test_graph_routes_a_proper_tool_call_through_tools_node_and_back_to_model():
    llm = fake_llm(
        AIMessage(
            content="", tool_calls=[{"name": "add_numbers", "args": {"a": 23, "b": 19}, "id": "1"}]
        ),
        AIMessage(content="42"),
    )

    with build_test_graph(llm) as app:
        result = app.invoke({"messages": [HumanMessage(content="23+19?")]}, config=_CONFIG)

    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1
    assert tool_messages[0].content == "42"
    assert result["messages"][-1].content == "42"


def test_graph_ends_immediately_when_model_answers_without_a_tool_call():
    llm = fake_llm(AIMessage(content="今天天氣很好"))

    with build_test_graph(llm) as app:
        result = app.invoke({"messages": [HumanMessage(content="今天天氣如何？")]}, config=_CONFIG)

    assert not any(isinstance(m, ToolMessage) for m in result["messages"])
    assert result["messages"][-1].content == "今天天氣很好"


def test_graph_retries_on_leaked_tool_call_text_until_a_clean_answer_arrives():
    leaked = "<|tool_call|>call:get_current_time{}"
    llm = fake_llm(
        AIMessage(content=leaked),
        AIMessage(content=leaked),
        AIMessage(content="現在是晚上八點"),
    )

    with build_test_graph(llm) as app:
        result = app.invoke({"messages": [HumanMessage(content="現在幾點？")]}, config=_CONFIG)

    assert result["messages"][-1].content == "現在是晚上八點"
    assert not any(isinstance(m, ToolMessage) for m in result["messages"])


def test_graph_recovers_a_leaked_tool_call_after_exhausting_retries():
    leaked = "<|tool_call|>call:add_numbers{a:2,b:3}"
    llm = fake_llm(
        AIMessage(content=leaked),
        AIMessage(content=leaked),
        AIMessage(content=leaked),
        AIMessage(content="答案是 5"),
    )

    with build_test_graph(llm) as app:
        result = app.invoke({"messages": [HumanMessage(content="2+3?")]}, config=_CONFIG)

    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1
    assert tool_messages[0].content == "5"
    assert result["messages"][-1].content == "答案是 5"

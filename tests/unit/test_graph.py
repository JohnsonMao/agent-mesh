"""Unit tests for build_graph's routing logic: tools_condition wiring and the best-effort
leaked tool-call recovery (a leaked tool-call is parsed on the first response, no retries)."""

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


def test_graph_recovers_a_leaked_tool_call_on_the_first_response():
    leaked = "<|tool_call|>call:add_numbers{a:2,b:3}"
    llm = fake_llm(
        AIMessage(content=leaked),
        AIMessage(content="答案是 5"),
    )

    with build_test_graph(llm) as app:
        result = app.invoke({"messages": [HumanMessage(content="2+3?")]}, config=_CONFIG)

    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1
    assert tool_messages[0].content == "5"
    assert result["messages"][-1].content == "答案是 5"


def test_graph_falls_back_to_raw_content_when_leaked_tool_call_cannot_be_parsed():
    leaked = "<|tool_call|>get_current_time somehow broken"
    llm = fake_llm(AIMessage(content=leaked))

    with build_test_graph(llm) as app:
        result = app.invoke({"messages": [HumanMessage(content="現在幾點？")]}, config=_CONFIG)

    assert not any(isinstance(m, ToolMessage) for m in result["messages"])
    assert result["messages"][-1].content == leaked

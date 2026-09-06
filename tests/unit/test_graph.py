"""Primary seam: the graph's app.invoke() boundary (conversation + tool-call loop)."""

import re
from datetime import UTC, datetime

from conftest import RecordingChatModel, build_test_graph, build_test_settings
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

import tools as tools_module
from main import SENT_AT_KEY, current_sent_at

SENT_AT_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


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
    assert tool_message.artifact == [{"title": "Weather", "url": "http://example.com"}]


def test_assistant_executes_command_via_explicit_tool_call() -> None:
    tool_call = {
        "name": "execute_command",
        "args": {"command": "echo 'running command'"},
        "id": "call-1",
    }
    responses = [
        AIMessage(content="", tool_calls=[tool_call]),
        AIMessage(content="Command finished successfully."),
    ]
    graph = build_test_graph(responses)
    config = {"configurable": {"thread_id": "t1", "user_id": "u1"}}

    result = graph.invoke({"messages": [HumanMessage(content="run echo")]}, config)

    assert result["messages"][-1].content == "Command finished successfully."
    tool_message = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert "Exit code: 0" in tool_message.content
    assert "running command" in tool_message.content


def test_assistant_loads_a_skill_via_explicit_tool_call(tmp_path) -> None:
    skill_dir = tmp_path / "greeting"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: greeting\ndescription: How to greet the user warmly.\n---\n\n"
        "Always greet enthusiastically.\n"
    )
    settings = build_test_settings(skills_dir=str(tmp_path))
    tool_call = {"name": "load_skill", "args": {"name": "greeting"}, "id": "call-1"}
    responses = [
        AIMessage(content="", tool_calls=[tool_call]),
        AIMessage(content="Got it, I'll greet you warmly."),
    ]
    graph = build_test_graph(responses, settings)
    config = {"configurable": {"thread_id": "t1", "user_id": "u1"}}

    result = graph.invoke({"messages": [HumanMessage(content="greet me")]}, config)

    assert result["messages"][-1].content == "Got it, I'll greet you warmly."
    tool_message = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert tool_message.content == "Always greet enthusiastically."


def test_assistant_gets_an_error_string_for_an_unknown_skill_name() -> None:
    tool_call = {"name": "load_skill", "args": {"name": "nonexistent"}, "id": "call-1"}
    responses = [
        AIMessage(content="", tool_calls=[tool_call]),
        AIMessage(content="Sorry, I couldn't find that."),
    ]
    graph = build_test_graph(responses)
    config = {"configurable": {"thread_id": "t1", "user_id": "u1"}}

    result = graph.invoke({"messages": [HumanMessage(content="use the nonexistent skill")]}, config)

    tool_message = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert "No skill named 'nonexistent' found" in tool_message.content


def test_current_sent_at_returns_utc_iso8601_string_with_millisecond_precision() -> None:
    expected_prefix = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M")[:15]
    sent_at = current_sent_at()
    assert sent_at.startswith(expected_prefix)
    assert sent_at.endswith("Z")
    assert SENT_AT_PATTERN.match(sent_at)


def test_only_human_messages_are_stamped_with_a_sent_at_timestamp() -> None:
    tool_call = {"name": "save_memory", "args": {"content": "User likes tea"}, "id": "call-1"}
    responses = [
        AIMessage(content="", tool_calls=[tool_call]),
        AIMessage(content="Got it, I'll remember that."),
    ]
    graph = build_test_graph(responses)
    config = {"configurable": {"thread_id": "t1", "user_id": "u1"}}
    human_message = HumanMessage(
        content="I like tea", additional_kwargs={SENT_AT_KEY: current_sent_at()}
    )

    result = graph.invoke({"messages": [human_message]}, config)

    human = next(message for message in result["messages"] if isinstance(message, HumanMessage))
    assert SENT_AT_PATTERN.match(human.additional_kwargs[SENT_AT_KEY])
    assert all(
        SENT_AT_KEY not in message.additional_kwargs
        for message in result["messages"]
        if not isinstance(message, HumanMessage)
    )


def test_a_message_without_a_sent_at_timestamp_is_passed_to_the_model_unprefixed() -> None:
    model = RecordingChatModel(messages=iter([AIMessage(content="ok")]))
    graph = build_test_graph([], llm=model)
    config = {"configurable": {"thread_id": "t1", "user_id": "u1"}}

    graph.invoke({"messages": [HumanMessage(content="hi")]}, config)

    received_human = next(m for m in model.received_messages[-1] if isinstance(m, HumanMessage))
    assert received_human.content == "hi"


def test_a_human_message_with_a_sent_at_timestamp_is_wrapped_in_current_datetime_for_the_model_but_stored_clean() -> (
    None
):
    model = RecordingChatModel(messages=iter([AIMessage(content="ok")]))
    graph = build_test_graph([], llm=model)
    config = {"configurable": {"thread_id": "t1", "user_id": "u1"}}
    human_message = HumanMessage(
        content="hi", additional_kwargs={SENT_AT_KEY: "2026-08-28T09:00:00.000Z"}
    )

    graph.invoke({"messages": [human_message]}, config)

    received_human = next(m for m in model.received_messages[-1] if isinstance(m, HumanMessage))
    assert (
        received_human.content
        == "<current_datetime>2026-08-28T09:00:00.000Z (Friday)</current_datetime>\nhi"
    )
    stored_human = graph.get_state(config).values["messages"][0]
    assert stored_human.content == "hi"


def test_an_ai_message_with_a_sent_at_timestamp_is_passed_to_the_model_without_timestamp_tag() -> (
    None
):
    model = RecordingChatModel(messages=iter([AIMessage(content="second turn response")]))
    graph = build_test_graph([], llm=model)
    config = {"configurable": {"thread_id": "t1", "user_id": "u1"}}
    history = [
        HumanMessage(content="hello", additional_kwargs={SENT_AT_KEY: "2026-08-28T08:55:00.000Z"}),
        AIMessage(content="hi there", additional_kwargs={SENT_AT_KEY: "2026-08-28T08:56:00.000Z"}),
        HumanMessage(
            content="follow up", additional_kwargs={SENT_AT_KEY: "2026-08-28T09:00:00.000Z"}
        ),
    ]

    graph.invoke({"messages": history}, config)

    received_ai = next(m for m in model.received_messages[-1] if isinstance(m, AIMessage))
    assert received_ai.content == "hi there"


def test_a_message_with_an_image_is_analyzed_before_reaching_the_model() -> None:
    model = RecordingChatModel(
        messages=iter(
            [AIMessage(content="A photo of a cat wearing a hat."), AIMessage(content="Cute cat!")]
        )
    )
    graph = build_test_graph([], llm=model)
    config = {"configurable": {"thread_id": "t1", "user_id": "u1"}}
    image_message = HumanMessage(
        content=[
            {"type": "text", "text": "what is this?"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA="}},
        ]
    )

    result = graph.invoke({"messages": [image_message]}, config)

    assert result["messages"][-1].content == "Cute cat!"
    stored_human = result["messages"][0]
    assert stored_human.content == "what is this?\n\n[圖片內容：A photo of a cat wearing a hat.]"
    received_human = next(m for m in model.received_messages[-1] if isinstance(m, HumanMessage))
    assert received_human.content.startswith("what is this?")
    assert "image_url" not in str(received_human.content)


def test_an_image_message_with_no_accompanying_text_is_still_analyzed() -> None:
    model = RecordingChatModel(
        messages=iter([AIMessage(content="A sunny beach."), AIMessage(content="Nice beach!")])
    )
    graph = build_test_graph([], llm=model)
    config = {"configurable": {"thread_id": "t1", "user_id": "u1"}}
    image_message = HumanMessage(
        content=[
            {"type": "text", "text": ""},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA="}},
        ]
    )

    result = graph.invoke({"messages": [image_message]}, config)

    stored_human = result["messages"][0]
    assert stored_human.content == "[圖片內容：A sunny beach.]"


def test_a_plain_text_message_skips_the_image_analysis_step() -> None:
    model = RecordingChatModel(messages=iter([AIMessage(content="ok")]))
    graph = build_test_graph([], llm=model)
    config = {"configurable": {"thread_id": "t1", "user_id": "u1"}}

    graph.invoke({"messages": [HumanMessage(content="hi")]}, config)

    # analyze_images and model share the same llm instance (see main.py), so a skipped
    # analyze_images step shows up as exactly one invoke() call, not two.
    assert len(model.received_messages) == 1

"""Seam: handle_slack_message (DM event in, post_reply call out)."""

from conftest import build_test_graph, build_test_settings
from langchain_core.messages import AIMessage

from slack_app import handle_slack_message


def test_replies_to_a_top_level_dm_using_its_own_ts_as_thread_id() -> None:
    graph = build_test_graph([AIMessage(content="Hello! How can I help?")])
    settings = build_test_settings()
    calls: list[tuple[str, str, str]] = []
    event = {"user": "U_ALLOWED", "channel": "D1", "ts": "111.1", "text": "hi"}

    handle_slack_message(
        event,
        graph=graph,
        settings=settings,
        post_reply=lambda channel, thread_ts, text: calls.append((channel, thread_ts, text)),
    )

    assert calls == [("D1", "111.1", "Hello! How can I help?")]


def test_replies_within_the_same_slack_thread_continue_the_same_conversation() -> None:
    graph = build_test_graph(
        [AIMessage(content="I'll remember that."), AIMessage(content="You like tea.")]
    )
    settings = build_test_settings()
    calls: list[tuple[str, str, str]] = []
    first_event = {"user": "U_ALLOWED", "channel": "D1", "ts": "111.1", "text": "I like tea"}
    reply_event = {
        "user": "U_ALLOWED",
        "channel": "D1",
        "ts": "111.2",
        "thread_ts": "111.1",
        "text": "what do I like?",
    }

    def post_reply(channel: str, thread_ts: str, text: str) -> None:
        calls.append((channel, thread_ts, text))

    handle_slack_message(first_event, graph=graph, settings=settings, post_reply=post_reply)
    handle_slack_message(reply_event, graph=graph, settings=settings, post_reply=post_reply)

    assert calls == [
        ("D1", "111.1", "I'll remember that."),
        ("D1", "111.1", "You like tea."),
    ]
    state = graph.get_state({"configurable": {"thread_id": "111.1", "user_id": "the-user"}})
    assert len(state.values["messages"]) == 4


def test_ignores_dms_from_non_whitelisted_users() -> None:
    graph = build_test_graph([AIMessage(content="should not be called")])
    settings = build_test_settings()
    calls: list[tuple[str, str, str]] = []
    event = {"user": "U_STRANGER", "channel": "D1", "ts": "111.1", "text": "hi"}

    handle_slack_message(
        event,
        graph=graph,
        settings=settings,
        post_reply=lambda channel, thread_ts, text: calls.append((channel, thread_ts, text)),
    )

    assert calls == []


def test_replies_with_a_short_error_message_when_the_graph_raises(monkeypatch) -> None:
    graph = build_test_graph([AIMessage(content="unused")])

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("LM Studio is unreachable")

    monkeypatch.setattr(graph, "invoke", boom)
    settings = build_test_settings()
    calls: list[tuple[str, str, str]] = []
    event = {"user": "U_ALLOWED", "channel": "D1", "ts": "111.1", "text": "hi"}

    handle_slack_message(
        event,
        graph=graph,
        settings=settings,
        post_reply=lambda channel, thread_ts, text: calls.append((channel, thread_ts, text)),
    )

    assert len(calls) == 1
    channel, thread_id, text = calls[0]
    assert (channel, thread_id) == ("D1", "111.1")
    assert "錯誤" in text

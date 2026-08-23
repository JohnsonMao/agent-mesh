import pytest
from langgraph.prebuilt import ToolRuntime
from pydantic import ValidationError

import learner_profile


class Item:
    def __init__(self, value: dict):
        self.value = value


class ProfileStore:
    def __init__(self):
        self.items: dict[tuple[str, str], dict] = {}

    def get(self, namespace, key):
        value = self.items.get((*namespace, key))
        return Item(value) if value is not None else None

    def put(self, namespace, key, value):
        self.items[(*namespace, key)] = value

    def delete(self, namespace, key):
        self.items.pop((*namespace, key), None)


def runtime(store: ProfileStore, user_id: str | None = "u1") -> ToolRuntime:
    context = {} if user_id is None else {"user_id": user_id}
    return ToolRuntime(
        state={},
        context=context,
        config={},
        stream_writer=lambda _: None,
        tool_call_id=None,
        store=store,
    )


def test_get_profile_returns_defaults_without_creating_profile():
    store = ProfileStore()

    result = learner_profile.get_profile.func(runtime(store))

    assert result == {
        "profile": {
            "level": "unknown",
            "learning_goals": [],
            "native_language": None,
            "interests": [],
            "correction_preference": "end_of_turn",
            "interface_language": "zh-TW",
            "target_language": "en",
        },
        "exists": False,
    }
    assert store.items == {}


def test_update_profile_preserves_unspecified_fields_and_normalizes_lists():
    store = ProfileStore()
    learner_profile.update_profile.func(
        runtime(store),
        level="B1",
        learning_goals=[" IELTS ", "ielts", "travel"],
        interface_language="en",
    )

    result = learner_profile.update_profile.func(runtime(store), interests=["Music"])

    assert result["created"] is False
    assert result["profile"]["level"] == "B1"
    assert result["profile"]["learning_goals"] == ["IELTS", "travel"]
    assert result["profile"]["interface_language"] == "en"
    assert result["profile"]["interests"] == ["Music"]


def test_empty_list_clears_existing_values():
    store = ProfileStore()
    learner_profile.update_profile.func(runtime(store), interests=["Music"])

    result = learner_profile.update_profile.func(runtime(store), interests=[])

    assert result["profile"]["interests"] == []


def test_profiles_are_isolated_by_user_id():
    store = ProfileStore()
    learner_profile.update_profile.func(runtime(store, "u1"), level="A2")

    other_user = learner_profile.get_profile.func(runtime(store, "u2"))

    assert other_user["exists"] is False
    assert other_user["profile"]["level"] == "unknown"


def test_delete_profile_then_update_recreates_it():
    store = ProfileStore()
    learner_profile.update_profile.func(runtime(store), level="C1")

    assert learner_profile.delete_profile.func(runtime(store)) == {"deleted": True}
    assert learner_profile.get_profile.func(runtime(store))["exists"] is False
    recreated = learner_profile.update_profile.func(runtime(store), level="B2")

    assert recreated["created"] is True
    assert recreated["profile"]["level"] == "B2"


def test_missing_context_returns_structured_error():
    store = ProfileStore()

    assert learner_profile.get_profile.func(runtime(store, None)) == {
        "error": "context_error",
        "message": "user_id is required in invocation Context",
    }


def test_empty_update_is_rejected_without_writing():
    store = ProfileStore()

    with pytest.raises(ValidationError):
        learner_profile.update_profile.func(runtime(store))

    assert store.items == {}

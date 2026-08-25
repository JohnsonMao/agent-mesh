"""Secondary seam: the save_memory tool's dedup/merge decision at the function boundary."""

from conftest import build_test_settings, build_test_store
from pytest import MonkeyPatch

import tools
from long_term_memory import memory_namespace


def test_save_memory_merges_a_near_duplicate_fact(monkeypatch: MonkeyPatch) -> None:
    settings = build_test_settings()
    store = build_test_store()
    namespace = memory_namespace("test-user")
    store.put(namespace, "existing-id", {"content": "User likes tea"})

    monkeypatch.setattr(tools, "get_store", lambda: store)
    monkeypatch.setattr(tools, "get_config", lambda: {"configurable": {"user_id": "test-user"}})
    monkeypatch.setattr(
        tools, "merge_memory_content", lambda settings, existing, new: "User loves tea (merged)"
    )
    save_memory = tools.make_save_memory(settings)

    result = save_memory.invoke({"content": "User likes tea"})

    assert "Updated existing memory" in result
    items = store.search(namespace, query="tea", limit=10)
    assert len(items) == 1
    assert items[0].value["content"] == "User loves tea (merged)"


def test_save_memory_keeps_unrelated_facts_separate(monkeypatch: MonkeyPatch) -> None:
    settings = build_test_settings()
    store = build_test_store()
    namespace = memory_namespace("test-user")
    store.put(namespace, "existing-id", {"content": "User likes tea"})

    monkeypatch.setattr(tools, "get_store", lambda: store)
    monkeypatch.setattr(tools, "get_config", lambda: {"configurable": {"user_id": "test-user"}})
    save_memory = tools.make_save_memory(settings)

    result = save_memory.invoke({"content": "User's favorite programming language is Python"})

    assert "Saved memory" in result
    items = store.search(namespace, query="python programming", limit=10)
    assert len(items) == 2

"""Unit tests for the plain tool functions (no LLM/graph involved)."""

from datetime import datetime

import tools
from tests.unit.conftest import FakeMemoryItem, FakeStore, fake_tool_runtime


def test_get_current_time_returns_parseable_timestamp():
    result = tools.get_current_time.func()

    datetime.strptime(result, "%Y-%m-%d %H:%M:%S")  # raises ValueError if malformed


def test_add_numbers_returns_sum():
    assert tools.add_numbers.func(23, 19) == 42


def test_save_memory_stores_new_entry_when_no_similar_memory(monkeypatch):
    store = FakeStore(search_results=[])

    result = tools.save_memory.func(
        "喜歡喝黑咖啡，不加糖", runtime=fake_tool_runtime(store)
    )

    assert result == "已記住這件事。"
    assert len(store.put_calls) == 1
    namespace, _key, value = store.put_calls[0]
    assert namespace == ("u1", "memories")
    assert value == {"content": "喜歡喝黑咖啡，不加糖"}


def test_save_memory_merges_near_duplicate(monkeypatch):
    existing = FakeMemoryItem(key="abc", value={"content": "喜歡喝拿鐵"}, score=0.95)
    store = FakeStore(search_results=[existing])
    monkeypatch.setattr(tools, "_merge_memory_content", lambda old, new: "合併後的內容")

    result = tools.save_memory.func(
        "喜歡喝黑咖啡不加糖", runtime=fake_tool_runtime(store)
    )

    assert result == "已合併既有的相似記憶。"
    assert store.put_calls == [(("u1", "memories"), "abc", {"content": "合併後的內容"})]


def test_save_memory_ignores_weak_match_and_stores_new_entry(monkeypatch):
    weak_match = FakeMemoryItem(key="abc", value={"content": "喜歡喝拿鐵"}, score=0.5)
    store = FakeStore(search_results=[weak_match])

    result = tools.save_memory.func("喜歡養貓", runtime=fake_tool_runtime(store))

    assert result == "已記住這件事。"
    assert len(store.put_calls) == 1
    namespace, key, _value = store.put_calls[0]
    assert key != "abc"


def test_recall_memory_returns_relevant_items_above_threshold(monkeypatch):
    items = [
        FakeMemoryItem(key="1", value={"content": "喜歡黑咖啡"}, score=0.8),
        FakeMemoryItem(key="2", value={"content": "不相關的記憶"}, score=0.1),
    ]
    store = FakeStore(search_results=items)

    result = tools.recall_memory.func("咖啡偏好", runtime=fake_tool_runtime(store))

    assert result == "- 喜歡黑咖啡"


def test_recall_memory_returns_fallback_message_when_nothing_relevant(monkeypatch):
    store = FakeStore(search_results=[])

    result = tools.recall_memory.func("任何東西", runtime=fake_tool_runtime(store))

    assert result == "沒有找到相關記憶。"

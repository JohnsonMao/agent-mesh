"""Shared fixtures/fakes for unit tests that must not touch a real LM Studio server."""

from contextlib import contextmanager

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.store.sqlite import SqliteStore

import main


class FakeToolChatModel(GenericFakeChatModel):
    """GenericFakeChatModel doesn't implement bind_tools; graph code needs it to be a no-op."""

    def bind_tools(self, tools, **kwargs):
        return self


class FakeMemoryItem:
    """Stand-in for langgraph.store.base.SearchItem, driven by whatever a test seeds."""

    def __init__(self, key: str, value: dict, score: float | None = None):
        self.key = key
        self.value = value
        self.score = score


class FakeStore:
    """Stand-in for BaseStore, driven entirely by pre-seeded search results."""

    def __init__(self, search_results: list[FakeMemoryItem] | None = None):
        self._search_results = search_results or []
        self.put_calls: list[tuple[tuple, str, dict]] = []

    def search(self, namespace, *, query=None, limit=10):
        return self._search_results[:limit]

    def put(self, namespace, key, value):
        self.put_calls.append((namespace, key, value))


def fake_config(user_id: str = "u1") -> dict:
    return {"configurable": {"user_id": user_id}}


@contextmanager
def build_test_graph(llm):
    """build_graph wired to throwaway in-memory sqlite checkpointer/store (see Q6)."""
    with (
        SqliteSaver.from_conn_string(":memory:") as checkpointer,
        SqliteStore.from_conn_string(":memory:") as store,
    ):
        yield main.build_graph(llm, checkpointer, store)


def fake_llm(*responses) -> FakeToolChatModel:
    return FakeToolChatModel(messages=iter(responses))

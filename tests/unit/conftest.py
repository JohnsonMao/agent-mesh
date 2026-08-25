"""Shared fixtures for unit tests: fake chat model, fake embeddings, test graph builder."""

import hashlib
from typing import Any

from langchain_core.embeddings import Embeddings
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import IndexConfig
from langgraph.store.memory import InMemoryStore

from config import Settings
from main import build_graph
from tools import make_tools


class FakeToolChatModel(GenericFakeChatModel):
    """A scripted chat model that supports bind_tools (a no-op passthrough)."""

    def bind_tools(self, tools: Any, **kwargs: Any) -> "FakeToolChatModel":
        return self


class FakeEmbeddings(Embeddings):
    """Deterministic bag-of-words embeddings; no real model or network call."""

    dims = 64

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dims
        for word in text.lower().split():
            index = int(hashlib.sha256(word.encode()).hexdigest(), 16) % self.dims
            vector[index] += 1.0
        return vector

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


def build_test_settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "lm_studio_base_url": "http://localhost:1234/v1",
        "lm_studio_model": "test-model",
        "lm_studio_embedding_model": "test-embedding",
        "model_temperature": 0.2,
        "model_max_tokens": 1024,
        "memory_top_k": 5,
        "memory_score_threshold": 0.4,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def build_test_store() -> InMemoryStore:
    return InMemoryStore(
        index=IndexConfig(dims=FakeEmbeddings.dims, embed=FakeEmbeddings(), fields=["content"])
    )


def build_test_graph(
    responses: list[AIMessage], settings: Settings | None = None
) -> CompiledStateGraph:
    settings = settings or build_test_settings()
    llm = FakeToolChatModel(messages=iter(responses))
    tools = make_tools(settings)
    checkpointer = InMemorySaver()
    store = build_test_store()
    return build_graph(llm, tools, checkpointer, store)

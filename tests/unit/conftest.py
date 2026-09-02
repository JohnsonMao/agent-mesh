"""Shared fixtures for unit tests: fake chat model, fake embeddings, test graph builder."""

import hashlib
from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.embeddings import Embeddings
from langchain_core.language_models import LanguageModelInput
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import IndexConfig
from langgraph.store.memory import InMemoryStore
from pydantic import Field

from config import Settings
from main import build_graph
from skills import load_skills
from tools import make_tools


class FakeToolChatModel(GenericFakeChatModel):
    """A scripted chat model that supports bind_tools (a no-op passthrough)."""

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        **kwargs: Any,
    ) -> "FakeToolChatModel":
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
        "model_provider": "openai",
        "lm_studio_base_url": "http://localhost:1234/v1",
        "lm_studio_model": "test-model",
        "lm_studio_embedding_model": "test-embedding",
        "model_temperature": 0.2,
        "model_max_tokens": 1024,
        "model_frequency_penalty": 0.3,
        "model_presence_penalty": 0.3,
        "memory_top_k": 5,
        "memory_score_threshold": 0.4,
        "database_url": "postgresql://postgres:postgres@localhost:5432/test_assistant",
        "skills_dir": "nonexistent-skills-dir",
        "slack_bot_token": "xoxb-test",
        "slack_app_token": "xapp-test",
        "slack_allowed_user_id": "U_ALLOWED",
        "disable_skill_scripts": False,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def build_test_store() -> InMemoryStore:
    return InMemoryStore(
        index=IndexConfig(dims=FakeEmbeddings.dims, embed=FakeEmbeddings(), fields=["content"])
    )


def build_test_graph(
    responses: list[AIMessage],
    settings: Settings | None = None,
    llm: FakeToolChatModel | None = None,
) -> CompiledStateGraph:
    settings = settings or build_test_settings()
    llm = llm or FakeToolChatModel(messages=iter(responses))
    skills = load_skills(settings.skills_dir)
    tools = make_tools(settings, skills)
    checkpointer = InMemorySaver()
    store = build_test_store()
    return build_graph(llm, tools, checkpointer, store, skills)


class RecordingChatModel(FakeToolChatModel):
    """Records each invoke() call's input messages, to assert what the model actually saw."""

    received_messages: list[list[BaseMessage]] = Field(default_factory=list)

    def invoke(
        self,
        input: LanguageModelInput,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> AIMessage:
        self.received_messages.append(list(input))  # type: ignore[arg-type]
        return super().invoke(input, config, **kwargs)

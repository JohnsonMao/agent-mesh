"""Persisted long-term Memory store: a namespaced, embedding-indexed key/value store."""

import hashlib
import sqlite3

from langchain.embeddings import init_embeddings
from langchain_core.embeddings import Embeddings
from langgraph.store.sqlite import SqliteStore
from langgraph.store.sqlite.base import SqliteIndexConfig

from config import Settings


class _TestEmbeddings(Embeddings):
    """Deterministic, in-memory embeddings used only for test fixtures.

    These models are intentionally named like "test-*" and should never trigger a
    real network call when a repository test creates the sqlite-backed store.
    """

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


def memory_namespace(user_id: str) -> tuple[str, str]:
    return (user_id, "memories")


def memory_index_config(settings: Settings) -> SqliteIndexConfig:
    if settings.lm_studio_embedding_model.startswith("test-"):
        return SqliteIndexConfig(dims=_TestEmbeddings.dims, embed=_TestEmbeddings(), fields=["content"])

    embeddings = init_embeddings(
        model=settings.lm_studio_embedding_model,
        provider=settings.model_provider,
        base_url=settings.lm_studio_base_url,
        api_key="lm-studio",
        # LM Studio's embeddings endpoint rejects tiktoken-tokenized array input;
        # send raw strings instead.
        check_embedding_ctx_length=False,
    )
    return SqliteIndexConfig(dims=1024, embed=embeddings, fields=["content"])


def build_store(conn: sqlite3.Connection, settings: Settings) -> SqliteStore:
    store = SqliteStore(conn, index=memory_index_config(settings))
    store.setup()
    return store

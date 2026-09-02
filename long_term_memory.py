"""Persisted long-term Memory store: a namespaced, embedding-indexed key/value store."""

import hashlib
from typing import Any

from langchain.embeddings import init_embeddings
from langchain_core.embeddings import Embeddings
from langgraph.store.postgres import AsyncPostgresStore, PostgresStore
from langgraph.store.postgres.base import PostgresIndexConfig
from psycopg.rows import dict_row

from config import Settings

DEFAULT_POOL_KWARGS: dict[str, Any] = {
    "autocommit": True,
    "prepare_threshold": 0,
    "row_factory": dict_row,
}


class _TestEmbeddings(Embeddings):
    """Deterministic, in-memory embeddings used only for test fixtures.

    These models are intentionally named like "test-*" and should never trigger a
    real network call when a repository test creates the store.
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


def memory_index_config(settings: Settings) -> PostgresIndexConfig:
    if settings.lm_studio_embedding_model.startswith("test-"):
        return PostgresIndexConfig(
            dims=_TestEmbeddings.dims,
            embed=_TestEmbeddings(),
            fields=["content"],
        )

    embeddings = init_embeddings(
        model=settings.lm_studio_embedding_model,
        provider=settings.model_provider,
        base_url=settings.lm_studio_base_url,
        api_key="lm-studio",
        # LM Studio's embeddings endpoint rejects tiktoken-tokenized array input;
        # send raw strings instead.
        check_embedding_ctx_length=False,
    )
    return PostgresIndexConfig(dims=1024, embed=embeddings, fields=["content"])


def build_store(conn: Any, settings: Settings) -> PostgresStore:  # noqa: ANN401
    store = PostgresStore(conn, index=memory_index_config(settings))
    store.setup()
    return store


async def build_async_store(conn: Any, settings: Settings) -> AsyncPostgresStore:  # noqa: ANN401
    store = AsyncPostgresStore(conn, index=memory_index_config(settings))
    await store.setup()
    return store

"""Persisted long-term Memory store: a namespaced, embedding-indexed key/value store."""

import sqlite3

from langchain.embeddings import init_embeddings
from langgraph.store.sqlite import SqliteStore
from langgraph.store.sqlite.base import SqliteIndexConfig

from config import Settings


def memory_namespace(user_id: str) -> tuple[str, str]:
    return (user_id, "memories")


def memory_index_config(settings: Settings) -> SqliteIndexConfig:
    embeddings = init_embeddings(
        f"openai:{settings.lm_studio_embedding_model}",
        base_url=settings.lm_studio_base_url,
        api_key="lm-studio",
    )
    return SqliteIndexConfig(dims=1024, embed=embeddings, fields=["content"])


def build_store(conn: sqlite3.Connection, settings: Settings) -> SqliteStore:
    store = SqliteStore(conn, index=memory_index_config(settings))
    store.setup()
    return store

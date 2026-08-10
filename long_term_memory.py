import os
import sys

from langchain_openai import OpenAIEmbeddings
from langgraph.store.base import IndexConfig
from langgraph.store.sqlite import SqliteStore
from pydantic import SecretStr

MEMORY_DB_PATH = "memory_store.sqlite"
MEMORY_INDEX_DIMS = 1024  # bge-m3 dense embedding size, served locally by LM Studio
MEMORY_TOP_K = 5
# store.search's LIMIT always fills up to MEMORY_TOP_K regardless of relevance, so this
# cosine-similarity cutoff drops weak matches that would otherwise pollute the prompt.
MEMORY_SCORE_THRESHOLD = 0.4


def memory_namespace(user_id: str) -> tuple[str, str]:
    """Namespace under which a user's long-term facts/preferences are stored."""
    return (user_id, "memories")


def build_memory_embeddings() -> OpenAIEmbeddings:
    """Embeddings client for semantic memory search, served locally by LM Studio."""
    return OpenAIEmbeddings(
        model=os.getenv("LM_STUDIO_EMBEDDING_MODEL", "bge-m3"),
        base_url=os.getenv("LM_STUDIO_BASE_URL", "http://localhost:1234/v1"),
        api_key=SecretStr("lm-studio"),
        check_embedding_ctx_length=False,
    )


def memory_index_config() -> IndexConfig:
    """Index config enabling vector search over the `content` field of stored memories."""
    return {"dims": MEMORY_INDEX_DIMS, "embed": build_memory_embeddings(), "fields": ["content"]}


def print_user_memories(
    user_id: str, query: str | None = None, db_path: str = MEMORY_DB_PATH
) -> None:
    """Print long-term memories for a user, ranked by semantic similarity when `query` is given."""
    with SqliteStore.from_conn_string(db_path, index=memory_index_config()) as store:
        namespace = memory_namespace(user_id)
        items = (
            store.search(namespace, query=query, limit=MEMORY_TOP_K)
            if query
            else store.search(namespace)
        )
        if not items:
            print(f"No memories found for user_id={user_id!r}")
            return

        for item in items:
            print(f"[{item.key}] {item.value.get('content')} (updated_at={item.updated_at})")


if __name__ == "__main__":
    args = sys.argv[1:]
    print_user_memories(
        args[0] if args else "demo-user",
        query=args[1] if len(args) > 1 else None,
    )

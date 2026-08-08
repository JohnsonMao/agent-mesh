import sys

from langgraph.store.sqlite import SqliteStore

MEMORY_DB_PATH = "memory_store.sqlite"


def memory_namespace(user_id: str) -> tuple[str, str]:
    """Namespace under which a user's long-term facts/preferences are stored."""
    return (user_id, "memories")


def print_user_memories(user_id: str, db_path: str = MEMORY_DB_PATH) -> None:
    """Print every long-term memory saved for a given user, across all threads."""
    with SqliteStore.from_conn_string(db_path) as store:
        items = store.search(memory_namespace(user_id))
        if not items:
            print(f"No memories found for user_id={user_id!r}")
            return

        for item in items:
            print(f"[{item.key}] {item.value.get('content')} (updated_at={item.updated_at})")


if __name__ == "__main__":
    print_user_memories(sys.argv[1] if len(sys.argv) > 1 else "demo-user")

import sys

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver

CHECKPOINT_DB_PATH = "checkpoints.sqlite"


def print_thread_history(thread_id: str, db_path: str = CHECKPOINT_DB_PATH) -> None:
    """Print the accumulated message history for a thread's latest checkpoint."""
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
    with SqliteSaver.from_conn_string(db_path) as checkpointer:
        # The latest checkpoint's messages already include every prior turn (add_messages reducer).
        latest = checkpointer.get_tuple(config)
        if latest is None:
            print(f"No checkpoints found for thread_id={thread_id!r}")
            return

        for message in latest.checkpoint["channel_values"].get("messages", []):
            print(f"{type(message).__name__}: {message.content}")


if __name__ == "__main__":
    print_thread_history(sys.argv[1] if len(sys.argv) > 1 else "demo-thread")

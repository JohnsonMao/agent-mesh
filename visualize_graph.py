"""Print/save a Mermaid diagram of the graph defined in main.py.

Usage:
    uv run python visualize_graph.py
"""

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from llm import build_llm
from main import build_graph

OUTPUT_PNG_PATH = "data/graph.png"


def main() -> None:
    # In-memory checkpointer/store are enough here since we only need the graph
    # structure, not real persistence.
    app = build_graph(build_llm(), InMemorySaver(), InMemoryStore())
    graph = app.get_graph()

    print(graph.draw_mermaid())

    try:
        png_bytes = graph.draw_mermaid_png()
    except Exception as exc:  # network/render failure (draw_mermaid_png calls mermaid.ink)
        print(f"無法產生 PNG（需要網路連線呼叫 mermaid.ink）：{exc}")
        return

    with open(OUTPUT_PNG_PATH, "wb") as f:
        f.write(png_bytes)
    print(f"已儲存圖片至 {OUTPUT_PNG_PATH}")


if __name__ == "__main__":
    main()

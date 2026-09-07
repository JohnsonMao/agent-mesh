from pathlib import Path

from conftest import build_test_graph
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

import graph_topology
from graph_topology import GENERATE_COMMAND, render_topology_markdown


def test_topology_document_uses_compiled_graph_and_sorted_tool_registry() -> None:
    graph = build_test_graph([AIMessage(content="must not be invoked")])
    @tool("zeta")
    def zeta() -> str:
        """Zeta."""
        return ""

    @tool("alpha")
    def alpha() -> str:
        """Alpha."""
        return ""

    registry = [zeta, alpha]

    markdown = render_topology_markdown(graph, registry)

    assert "__start__ -->|image input| analyze_images" in markdown
    assert "model -->|response complete| __end__" in markdown
    assert "budget -->|absolute limit| __end__" in markdown
    assert markdown.index("- `alpha`") < markdown.index("- `zeta`")
    assert "generation-time Tool registry" in markdown
    assert "20" in markdown and "40" in markdown and "60" in markdown
    assert "Graph Topology" in markdown
    assert "Execution Path" in markdown
    assert "Execution Trace" in markdown


def test_committed_topology_document_is_fresh() -> None:
    from graph_topology import build_document_graph_and_tools

    graph, tools = build_document_graph_and_tools()
    expected = render_topology_markdown(graph, tools)
    actual = Path("docs/graph-topology.md").read_text()

    assert actual == expected, f"Topology document is stale; run `{GENERATE_COMMAND}`"


def test_standalone_generator_writes_with_local_dependencies(tmp_path, monkeypatch) -> None:
    output = tmp_path / "graph-topology.md"
    monkeypatch.setattr(graph_topology, "DOCUMENT_PATH", output)

    graph_topology.main()

    assert output.read_text() == render_topology_markdown(
        *graph_topology.build_document_graph_and_tools()
    )

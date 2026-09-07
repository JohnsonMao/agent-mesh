"""Generate the local, reviewable Graph Topology documentation."""

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from langchain_core.language_models import LanguageModelInput
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.memory import InMemoryStore

from config import load_settings
from graph_nodes import FORMAL_GRAPH_NODES
from main import build_graph
from skills import load_skills
from tools import make_tools

DOCUMENT_PATH = Path("docs/graph-topology.md")
GENERATE_COMMAND = "uv run python graph_topology.py"


class _DocumentationModel(BaseChatModel):
    """A graph-construction dependency that makes accidental invocation explicit."""

    @property
    def _llm_type(self) -> str:
        return "documentation-only"

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        del tools, tool_choice, kwargs
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: object | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del messages, stop, run_manager, kwargs
        raise RuntimeError("The topology generator must not invoke the model")


def build_document_graph_and_tools() -> tuple[CompiledStateGraph, list[BaseTool]]:
    """Assemble the production graph locally with its production Tool registry."""
    settings = load_settings()
    skills = load_skills(settings.skills_dir)
    tools = make_tools(settings, skills)
    graph = build_graph(
        _DocumentationModel(), tools, InMemorySaver(), InMemoryStore(), skills
    )
    return graph, tools


def render_topology_markdown(graph: CompiledStateGraph, tools: Sequence[BaseTool]) -> str:
    inspected = graph.get_graph()
    formal_nodes = [name for name in inspected.nodes if name in FORMAL_GRAPH_NODES]
    edges = sorted(
        inspected.edges,
        key=lambda edge: (edge.source, edge.target, str(edge.data or "")),
    )
    diagram_lines = ["flowchart TD"]
    diagram_lines.extend(f"    {name}[{name}]" for name in formal_nodes)
    for edge in edges:
        label = f"|{edge.data}|" if edge.data else ""
        diagram_lines.append(f"    {edge.source} -->{label} {edge.target}")

    tool_names = sorted({str(tool.name) for tool in tools})
    inventory = "\n".join(f"- `{name}`" for name in tool_names)
    diagram = "\n".join(diagram_lines)
    return f"""# Assistant Graph Topology

This Graph Topology documents every route the Assistant may take. It is not an
Execution Path taken by a particular Turn. Phoenix retains that Execution Path
inside the fuller Execution Trace, which also includes model and Tool evidence.

```mermaid
{diagram}
```

The displayed names are exact runtime identities. `__start__` and `__end__` are
LangGraph boundaries; the formal Assistant nodes are `{formal_nodes[0]}`,
`{formal_nodes[1]}`, `{formal_nodes[2]}`, and `{formal_nodes[3]}`.

## Tool inventory

This is the deterministic, generation-time Tool registry used to construct the
graph. Runtime configuration may make Tool availability differ by environment.

{inventory}

## Recursion budget

The `budget` node pauses for a user decision after 20 and 40 Tool steps and ends
the Turn at the absolute limit of 60 Tool steps. These are node behaviors, not
additional graph nodes or edges.

Regenerate locally with `{GENERATE_COMMAND}`. Mermaid source remains local; this
workflow does not use Mermaid.ink or any hosted renderer.
"""


def main() -> None:
    graph, tools = build_document_graph_and_tools()
    DOCUMENT_PATH.write_text(render_topology_markdown(graph, tools))


if __name__ == "__main__":
    main()

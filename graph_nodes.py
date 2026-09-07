"""Stable runtime identities for the Assistant's formal graph nodes."""

from typing import Literal

MODEL_NODE = "model"
TOOLS_NODE = "tools"
IMAGE_ANALYSIS_NODE = "analyze_images"
BUDGET_NODE = "budget"

FORMAL_GRAPH_NODES = (MODEL_NODE, TOOLS_NODE, IMAGE_ANALYSIS_NODE, BUDGET_NODE)

RouteOutcome = Literal[
    "image input",
    "ordinary input",
    "tool call",
    "response complete",
    "budget available",
    "absolute limit",
]
IMAGE_INPUT_ROUTE: RouteOutcome = "image input"
ORDINARY_INPUT_ROUTE: RouteOutcome = "ordinary input"
TOOL_CALL_ROUTE: RouteOutcome = "tool call"
RESPONSE_COMPLETE_ROUTE: RouteOutcome = "response complete"
BUDGET_AVAILABLE_ROUTE: RouteOutcome = "budget available"
ABSOLUTE_LIMIT_ROUTE: RouteOutcome = "absolute limit"

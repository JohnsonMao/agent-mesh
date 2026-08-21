"""Best-effort recovery of a malformed tool-call some local models leak as plain text
instead of a proper tool_calls entry."""

import json
import re
from typing import Any

LEAKED_TOOL_CALL_PATTERN = re.compile(r"<\|?tool_call\|?>")
# e.g. "<|tool_call>call:add_numbers{a:23,b:19}" -> name="add_numbers", args="a:23,b:19"
LEAKED_TOOL_CALL_DETAIL_PATTERN = re.compile(r"call:(?P<name>\w+)\{(?P<args>[^}]*)\}")


def parse_leaked_tool_call(content: str) -> dict[str, Any] | None:
    """Best-effort recovery of a malformed tool-call leaked as plain text content."""
    match = LEAKED_TOOL_CALL_DETAIL_PATTERN.search(content)
    if not match:
        return None

    args: dict[str, Any] = {}
    for pair in match.group("args").split(","):
        key, _, value = pair.partition(":")
        key, value = key.strip(), value.strip()
        if not key:
            continue
        try:
            args[key] = json.loads(value)
        except json.JSONDecodeError:
            args[key] = value
    return {"name": match.group("name"), "args": args}

"""Reduce Playwright CLI output before it becomes Conversation-visible context."""

import re
from collections.abc import Mapping
from dataclasses import dataclass

from execution_traces import safe_value

PAGE_EVIDENCE_LIMIT = 6_000
RECENT_ERROR_LIMIT = 800
_URL = re.compile(r"(?:Page URL|URL)\s*:\s*(\S+)", re.I)
_TITLE = re.compile(r"(?:Page Title|Title)\s*:\s*(.+)", re.I)
_SESSION = re.compile(r"(?:^|\s)-s(?:=|\s+)([^\s]+)")


@dataclass(frozen=True)
class BrowserReduction:
    state: dict[str, str]
    model_content: str


def is_playwright_command(command: str) -> bool:
    return bool(re.search(r"(?:^|\s)(?:npx\s+)?playwright-cli(?:\s|$)", command))


def reduce_browser_command(
    command: str, output: str, previous: Mapping[str, str] | None
) -> BrowserReduction | None:
    """Return compact Browser State for Playwright only; leave other commands untouched."""
    if not is_playwright_command(command):
        return None

    safe_output = str(safe_value(output, payload_limit=PAGE_EVIDENCE_LIMIT))
    if _failed(output):
        state = dict(previous or {})
        state["recent_error"] = safe_output[:RECENT_ERROR_LIMIT]
        return BrowserReduction(state, _format_state(state))

    state = {
        "url": _matched(_URL, safe_output) or str((previous or {}).get("url", "unknown")),
        "title": _matched(_TITLE, safe_output) or str((previous or {}).get("title", "unknown")),
        "evidence": safe_output[:PAGE_EVIDENCE_LIMIT],
        "recent_operation": _operation(command),
    }
    session = _matched(_SESSION, command)
    if session:
        state["session"] = session
    elif previous and previous.get("session"):
        state["session"] = previous["session"]
    return BrowserReduction(state, _format_state(state))


def _failed(output: str) -> bool:
    return bool(
        re.search(r"(?:^|\n)Exit code:\s*[1-9]\d*", output)
        or " timed out after " in output
    )


def _matched(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(1).strip() if match else None


def _operation(command: str) -> str:
    parts = command.split()
    try:
        index = next(i for i, part in enumerate(parts) if part.endswith("playwright-cli"))
        return " ".join(parts[index + 1 : index + 3]) or "playwright-cli"
    except StopIteration:
        return "playwright-cli"


def _format_state(state: Mapping[str, str]) -> str:
    if not state:
        return "Browser operation failed before a valid Browser State was available."
    lines = ["Browser State (bounded; not a complete page):"]
    for key in ("session", "url", "title", "recent_operation", "recent_error", "evidence"):
        value = state.get(key)
        if value:
            label = key.replace("_", " ").title()
            lines.append(f"{label}: {value}")
    return "\n".join(lines)

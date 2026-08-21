"""Turn/Run-level LLM & tool call stats: collection (via a callback handler) and summarization."""

import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import BaseMessage
from langchain_core.outputs import LLMResult


@dataclass
class CallStat:
    """One LLM invocation's token usage and latency, tagged by call_kind (chat/memory_merge)."""

    kind: str
    duration_seconds: float
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None


@dataclass
class ToolStat:
    """One tool invocation's latency."""

    tool_name: str
    duration_seconds: float


def summarize_call_stats(call_stats: list[CallStat]) -> str:
    chat_calls = sum(1 for c in call_stats if c.kind == "chat")
    merge_calls = sum(1 for c in call_stats if c.kind == "memory_merge")
    llm_seconds = sum(c.duration_seconds for c in call_stats)
    prompt_tokens = sum(c.prompt_tokens or 0 for c in call_stats)
    completion_tokens = sum(c.completion_tokens or 0 for c in call_stats)
    total_tokens = sum(c.total_tokens or 0 for c in call_stats)
    return (
        f"llm={llm_seconds:.2f}s ({chat_calls} chat + {merge_calls} memory_merge calls) | "
        f"tokens: prompt={prompt_tokens} completion={completion_tokens} total={total_tokens}"
    )


def summarize_tool_stats(tool_stats: list[ToolStat]) -> str:
    tool_seconds = sum(t.duration_seconds for t in tool_stats)
    return f"tool={tool_seconds:.2f}s ({len(tool_stats)} calls)"


class LoggingCallbackHandler(BaseCallbackHandler):
    """Hooks into the chat model / tool lifecycle to log execution events and collect Turn-level stats."""

    def __init__(self) -> None:
        self.call_stats: list[CallStat] = []
        self.tool_stats: list[ToolStat] = []
        # Keyed by run_id so on_*_end can match back to the corresponding on_*_start.
        self._call_starts: dict[UUID, tuple[float, dict[str, Any]]] = {}
        self._tool_starts: dict[UUID, tuple[float, str]] = {}

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        *,
        run_id: UUID,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        print(f"[hook] model start: {len(messages[0])} messages")
        self._call_starts[run_id] = (time.monotonic(), metadata or {})

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        print("[hook] model end")
        started_at, metadata = self._call_starts.pop(run_id, (None, {}))
        duration = time.monotonic() - started_at if started_at is not None else 0.0
        usage = (response.llm_output or {}).get("token_usage") or {}
        self.call_stats.append(
            CallStat(
                kind=metadata.get("call_kind", "chat"),
                duration_seconds=duration,
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
            )
        )

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        name = serialized.get("name", "unknown")
        print(f"[hook] tool start: {name}({input_str})")
        self._tool_starts[run_id] = (time.monotonic(), name)

    def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
        print(f"[hook] tool end: {output}")
        started_at, name = self._tool_starts.pop(run_id, (None, "unknown"))
        duration = time.monotonic() - started_at if started_at is not None else 0.0
        self.tool_stats.append(ToolStat(tool_name=name, duration_seconds=duration))

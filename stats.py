"""Self-built observability: per-call latency/token stats for LLM and tool calls."""

import time
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult


@dataclass
class CallStat:
    duration_seconds: float
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class ToolStat:
    tool_name: str
    duration_seconds: float


@dataclass(eq=False)
class LoggingCallbackHandler(BaseCallbackHandler):
    call_stats: list[CallStat] = field(default_factory=list)
    tool_stats: list[ToolStat] = field(default_factory=list)
    _llm_starts: dict[UUID, float] = field(default_factory=dict)
    _tool_starts: dict[UUID, tuple[str, float]] = field(default_factory=dict)

    def on_chat_model_start(
        self, serialized: dict[str, Any], messages: Any, *, run_id: UUID, **kwargs: Any
    ) -> None:
        self._llm_starts[run_id] = time.monotonic()

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        started_at = self._llm_starts.pop(run_id, None)
        duration = time.monotonic() - started_at if started_at is not None else 0.0
        usage = (response.llm_output or {}).get("token_usage", {})
        self.call_stats.append(
            CallStat(
                duration_seconds=duration,
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
            )
        )

    def on_tool_start(
        self, serialized: dict[str, Any], input_str: str, *, run_id: UUID, **kwargs: Any
    ) -> None:
        tool_name = serialized.get("name", "unknown")
        self._tool_starts[run_id] = (tool_name, time.monotonic())

    def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
        started = self._tool_starts.pop(run_id, None)
        if started is None:
            return
        tool_name, started_at = started
        self.tool_stats.append(
            ToolStat(tool_name=tool_name, duration_seconds=time.monotonic() - started_at)
        )


def summarize_call_stats(call_stats: list[CallStat]) -> dict[str, float]:
    if not call_stats:
        return {
            "count": 0,
            "total_duration_seconds": 0.0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }
    return {
        "count": len(call_stats),
        "total_duration_seconds": sum(c.duration_seconds for c in call_stats),
        "prompt_tokens": sum(c.prompt_tokens for c in call_stats),
        "completion_tokens": sum(c.completion_tokens for c in call_stats),
    }


def summarize_tool_stats(tool_stats: list[ToolStat]) -> dict[str, dict[str, float]]:
    summary: dict[str, dict[str, float]] = {}
    for stat in tool_stats:
        entry = summary.setdefault(stat.tool_name, {"count": 0, "total_duration_seconds": 0.0})
        entry["count"] += 1
        entry["total_duration_seconds"] += stat.duration_seconds
    return summary

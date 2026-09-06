"""Adapter around Slack's chat-stream API: the Thinking Step-per-Task-Card lifecycle.

A `ThinkingStream` is opened once per Turn (see CONTEXT.md) and carries every Thinking
Step's Task Card update plus the final reply, so the whole Turn renders as one
continuously-updating Slack message instead of a status line followed by a reply.
"""

from collections.abc import Awaitable, Callable
from typing import Protocol

from slack_sdk.models.blocks.block_elements import UrlSourceElement
from slack_sdk.models.messages.chunk import TaskUpdateChunk

SLACK_SEGMENT_TEXT_LIMIT = 11_500
_SEGMENT_CONTENT_LIMIT = 11_450
TASK_CARD_OUTPUT_LIMIT = 1_000
TASK_CARD_TRUNCATION_NOTICE = "\n[摘要已截斷]"


def split_reply_segments(markdown_text: str) -> list[str]:
    """Split a reply without loss, preferring a complete paragraph per segment."""
    if not markdown_text:
        return [markdown_text]
    segments: list[str] = []
    remaining = markdown_text
    while len(remaining) > _SEGMENT_CONTENT_LIMIT:
        boundary = remaining.rfind("\n\n", 0, _SEGMENT_CONTENT_LIMIT + 1)
        cut_at = boundary + 2 if boundary >= 0 else _SEGMENT_CONTENT_LIMIT
        segments.append(remaining[:cut_at])
        remaining = remaining[cut_at:]
    segments.append(remaining)
    return segments


def numbered_reply_segments(markdown_text: str) -> list[str]:
    """Give multi-message replies an ordered, Slack-safe marker on every segment."""
    segments = split_reply_segments(markdown_text)
    if len(segments) == 1:
        return segments
    total = len(segments)
    return [f"（第 {index}/{total} 段）\n\n{segment}" for index, segment in enumerate(segments, 1)]


def bounded_task_card_output(output: str | None) -> str | None:
    if output is None or len(output) <= TASK_CARD_OUTPUT_LIMIT:
        return output
    limit = TASK_CARD_OUTPUT_LIMIT - len(TASK_CARD_TRUNCATION_NOTICE)
    return output[:limit] + TASK_CARD_TRUNCATION_NOTICE


def bounded_final_text(markdown_text: str) -> tuple[str, int, bool]:
    """Compatibility telemetry helper describing first-segment delivery."""
    segments = numbered_reply_segments(markdown_text)
    return segments[0], len(markdown_text), len(segments) > 1


class RawChatStream(Protocol):
    """The subset of slack_sdk's AsyncChatStream this module depends on."""

    async def append(self, *, chunks: list[TaskUpdateChunk]) -> object: ...
    async def stop(self, *, markdown_text: str) -> object: ...


class ThinkingStream(Protocol):
    async def update_task(
        self,
        task_id: str,
        title: str,
        status: str,
        output: str | None = None,
        sources: list[UrlSourceElement] | None = None,
    ) -> None: ...

    async def finish(self, markdown_text: str) -> None: ...


OpenThinkingStream = Callable[[str, str], Awaitable[ThinkingStream]]


class SlackThinkingStream:
    """Tracks in-progress Task Cards so `finish()` can resolve any still stuck.

    A Turn that gets cancelled or raises may leave its most recent Task Card at
    "in_progress"; without this, that card would stay stuck forever once the stream
    is stopped.
    """

    def __init__(self, stream: RawChatStream) -> None:
        self._stream = stream
        self._pending: dict[str, str] = {}

    async def update_task(
        self,
        task_id: str,
        title: str,
        status: str,
        output: str | None = None,
        sources: list[UrlSourceElement] | None = None,
    ) -> None:
        if status == "in_progress":
            self._pending[task_id] = title
        else:
            self._pending.pop(task_id, None)
        chunk = TaskUpdateChunk(
            id=task_id,
            title=title,
            status=status,
            output=bounded_task_card_output(output),
            sources=sources,
        )
        await self._stream.append(chunks=[chunk])

    async def finish(self, markdown_text: str) -> None:
        for task_id, title in self._pending.items():
            await self._stream.append(
                chunks=[TaskUpdateChunk(id=task_id, title=title, status="error")]
            )
        self._pending.clear()
        first_segment, _, _ = bounded_final_text(markdown_text)
        await self._stream.stop(markdown_text=first_segment)

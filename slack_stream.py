"""Adapter around Slack's chat-stream API: the Thinking Step-per-Task-Card lifecycle.

A `ThinkingStream` is opened once per Turn (see CONTEXT.md) and carries every Thinking
Step's Task Card update plus the final reply, so the whole Turn renders as one
continuously-updating Slack message instead of a status line followed by a reply.
"""

from collections.abc import Awaitable, Callable
from typing import Protocol

from slack_sdk.models.blocks.block_elements import UrlSourceElement
from slack_sdk.models.messages.chunk import TaskUpdateChunk

SLACK_FINAL_TEXT_LIMIT = 35_000
TRUNCATION_NOTICE = "[回覆因長度限制已截斷；如需後續內容，請要求我繼續。]"


def bounded_final_text(markdown_text: str) -> tuple[str, int, bool]:
    """Return Slack-safe final text without changing the model's stored reply."""
    original_length = len(markdown_text)
    if original_length <= SLACK_FINAL_TEXT_LIMIT:
        return markdown_text, original_length, False
    limit = SLACK_FINAL_TEXT_LIMIT - len(TRUNCATION_NOTICE)
    return markdown_text[:limit] + TRUNCATION_NOTICE, original_length, True


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
            id=task_id, title=title, status=status, output=output, sources=sources
        )
        await self._stream.append(chunks=[chunk])

    async def finish(self, markdown_text: str) -> None:
        for task_id, title in self._pending.items():
            await self._stream.append(
                chunks=[TaskUpdateChunk(id=task_id, title=title, status="error")]
            )
        self._pending.clear()
        bounded_text, _, _ = bounded_final_text(markdown_text)
        await self._stream.stop(markdown_text=bounded_text)

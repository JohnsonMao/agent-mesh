"""Seam: SlackThinkingStream wrapping the raw append/stop chat-stream calls."""

from slack_sdk.models.messages.chunk import TaskUpdateChunk

from slack_stream import (
    SLACK_SEGMENT_TEXT_LIMIT,
    TASK_CARD_OUTPUT_LIMIT,
    SlackThinkingStream,
    split_reply_segments,
)


class FakeRawStream:
    def __init__(self) -> None:
        self.appended: list[list[TaskUpdateChunk]] = []
        self.stopped_with: str | None = None

    async def append(self, *, chunks: list[TaskUpdateChunk]) -> None:
        self.appended.append(chunks)

    async def stop(self, *, markdown_text: str) -> None:
        self.stopped_with = markdown_text


async def test_update_task_appends_a_task_update_chunk() -> None:
    raw = FakeRawStream()
    stream = SlackThinkingStream(raw)

    await stream.update_task("run-1", "🔍 正在搜尋網路…", "in_progress")

    assert len(raw.appended) == 1
    (chunk,) = raw.appended[0]
    assert chunk.id == "run-1"
    assert chunk.title == "🔍 正在搜尋網路…"
    assert chunk.status == "in_progress"


async def test_update_task_passes_through_output_and_sources() -> None:
    raw = FakeRawStream()
    stream = SlackThinkingStream(raw)

    await stream.update_task(
        "run-1",
        "🔍 搜尋網路",
        "complete",
        output="Weather",
        sources=[{"url": "http://example.com", "text": "Weather"}],
    )

    (chunk,) = raw.appended[0]
    assert chunk.output == "Weather"
    assert chunk.sources == [{"url": "http://example.com", "text": "Weather"}]


async def test_update_task_bounds_large_output_with_an_explicit_summary_marker() -> None:
    raw = FakeRawStream()
    stream = SlackThinkingStream(raw)

    await stream.update_task("run-1", "💻 執行指令", "complete", output="x" * 1_001)

    (chunk,) = raw.appended[0]
    assert len(chunk.output) <= TASK_CARD_OUTPUT_LIMIT
    assert chunk.output.endswith("[摘要已截斷]")


async def test_finish_stops_the_stream_with_the_final_text() -> None:
    raw = FakeRawStream()
    stream = SlackThinkingStream(raw)
    await stream.update_task("run-1", "🔍 搜尋網路", "complete")

    await stream.finish("Here's the answer.")

    assert raw.stopped_with == "Here's the answer."
    # the completed task should not be re-resolved as an error
    assert len(raw.appended) == 1


def test_split_reply_segments_prefers_paragraph_boundaries_and_preserves_content() -> None:
    reply = "first paragraph\n\n" + "second paragraph" * 1_000

    segments = split_reply_segments(reply)

    assert "".join(segments) == reply
    assert segments[0] == "first paragraph\n\n"
    assert all(len(segment) <= SLACK_SEGMENT_TEXT_LIMIT for segment in segments)


async def test_finish_uses_the_first_safe_reply_segment() -> None:
    raw = FakeRawStream()
    stream = SlackThinkingStream(raw)
    reply = "a" * (SLACK_SEGMENT_TEXT_LIMIT + 1)

    await stream.finish(reply)

    assert raw.stopped_with is not None
    assert raw.stopped_with.startswith("（第 1/2 段）\n\n")
    assert len(raw.stopped_with) <= SLACK_SEGMENT_TEXT_LIMIT


async def test_finish_resolves_any_still_in_progress_task_to_error_first() -> None:
    raw = FakeRawStream()
    stream = SlackThinkingStream(raw)
    await stream.update_task("run-1", "🔍 正在搜尋網路…", "in_progress")

    await stream.finish("已收到你的補充，重新整理回覆中…")

    assert len(raw.appended) == 2
    (chunk,) = raw.appended[1]
    assert chunk.id == "run-1"
    assert chunk.title == "🔍 正在搜尋網路…"
    assert chunk.status == "error"
    assert raw.stopped_with == "已收到你的補充，重新整理回覆中…"


async def test_finish_only_resolves_each_stuck_task_once_even_if_called_twice() -> None:
    raw = FakeRawStream()
    stream = SlackThinkingStream(raw)
    await stream.update_task("run-1", "🔍 正在搜尋網路…", "in_progress")

    await stream.finish("first finish")
    await stream.finish("second finish")

    assert len(raw.appended) == 2

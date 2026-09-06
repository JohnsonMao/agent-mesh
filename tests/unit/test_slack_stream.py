"""Seam: SlackThinkingStream wrapping the raw append/stop chat-stream calls."""

from slack_sdk.models.messages.chunk import TaskUpdateChunk

from slack_stream import SlackThinkingStream


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


async def test_finish_stops_the_stream_with_the_final_text() -> None:
    raw = FakeRawStream()
    stream = SlackThinkingStream(raw)
    await stream.update_task("run-1", "🔍 搜尋網路", "complete")

    await stream.finish("Here's the answer.")

    assert raw.stopped_with == "Here's the answer."
    # the completed task should not be re-resolved as an error
    assert len(raw.appended) == 1


async def test_finish_truncates_an_overlong_reply_before_stopping_the_stream() -> None:
    raw = FakeRawStream()
    stream = SlackThinkingStream(raw)

    await stream.finish("a" * 35_100)

    assert raw.stopped_with is not None
    assert len(raw.stopped_with) <= 35_000
    assert raw.stopped_with.endswith("[回覆因長度限制已截斷；如需後續內容，請要求我繼續。]")


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

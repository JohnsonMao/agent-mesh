"""Public trace repository and viewer behavior."""

from datetime import UTC, datetime, timedelta

from aiohttp.test_utils import TestClient, TestServer

from execution_traces import InMemoryTraceRepository, TraceRecorder, create_viewer_app


async def test_viewer_lists_filtered_traces_and_orders_latest_first() -> None:
    repository = InMemoryTraceRepository()
    recorder = TraceRecorder(repository, now=lambda: datetime(2026, 1, 2, tzinfo=UTC))
    old = await recorder.start_trace("conversation-a", "old input")
    await recorder.finish_trace(old, "completed", output="old reply")
    recorder.now = lambda: datetime(2026, 1, 3, tzinfo=UTC)
    failed = await recorder.start_trace("conversation-b", "new input")
    await recorder.finish_trace(failed, "failed", error="model unavailable")

    server = TestServer(create_viewer_app(repository))
    client = TestClient(server)
    await client.start_server()
    try:
        response = await client.get(
            "/traces?status=failed&conversation_id=conversation-b&started_after=2026-01-02T12:00:00Z"
        )
        page = await response.text()
    finally:
        await client.close()

    assert response.status == 200
    assert failed.trace_id in page
    assert old.trace_id not in page
    assert 'name="started_after"' in page
    assert "setInterval(() => location.reload(), 5000)" in page


async def test_viewer_displays_ordered_safe_step_timeline() -> None:
    repository = InMemoryTraceRepository()
    recorder = TraceRecorder(repository, now=lambda: datetime(2026, 1, 2, tzinfo=UTC))
    trace = await recorder.start_trace("conversation-a", "TOKEN=top-secret")
    await recorder.start_step(trace, "step-model", None, "model", {"api_key": "abc"})
    recorder.now = lambda: datetime(2026, 1, 2, 0, 0, 2, tzinfo=UTC)
    await recorder.finish_step(trace, "step-model", "completed", output={"text": "hello"})
    await recorder.start_step(trace, "step-tool", "step-model", "tool", {"command": "echo ok"})
    await recorder.finish_step(trace, "step-tool", "completed", output="done")
    await recorder.start_step(trace, "step-live", None, "model")
    await recorder.finish_trace(trace, "completed", output="final answer")

    server = TestServer(create_viewer_app(repository))
    client = TestClient(server)
    await client.start_server()
    try:
        response = await client.get(f"/traces/{trace.trace_id}")
        page = await response.text()
    finally:
        await client.close()

    assert response.status == 200
    assert "final answer" in page
    assert "top-secret" not in page
    assert "abc" not in page
    assert page.index("step-model") < page.index("step-tool")
    assert "2.000s" in page
    assert "in progress" in page


async def test_recorder_truncates_command_output_and_expires_content_before_metadata() -> None:
    now = datetime(2026, 3, 1, tzinfo=UTC)
    repository = InMemoryTraceRepository()
    recorder = TraceRecorder(repository, now=lambda: now, command_output_limit=12)
    trace = await recorder.start_trace("conversation-a", "Bearer abc.def.ghi")
    await recorder.start_step(trace, "command", None, "tool", {"name": "execute_command"})
    await recorder.finish_step(trace, "command", "completed", output="x" * 30)
    await recorder.finish_trace(trace, "completed", output="Slack xoxb-123456789012-abcdefgh")

    await recorder.cleanup(now + timedelta(days=15))
    content_expired = await repository.get_trace(trace.trace_id)
    assert content_expired is not None
    assert content_expired.input is None
    assert content_expired.output is None
    assert content_expired.steps[0].output is None

    await recorder.cleanup(now + timedelta(days=91))
    assert await repository.get_trace(trace.trace_id) is None

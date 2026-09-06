"""Public telemetry adapter contract."""

import asyncio
from datetime import UTC, datetime, timedelta

from execution_traces import (
    BoundedBatchExporter,
    InMemorySpanExporter,
    PersistentAlertPolicy,
    TelemetrySpan,
    TraceRecorder,
    _otlp_request,
)


async def test_one_turn_exports_a_redacted_root_and_parented_child_span() -> None:
    collector = InMemorySpanExporter()
    recorder = TraceRecorder(
        BoundedBatchExporter(collector), now=lambda: datetime(2026, 1, 2, tzinfo=UTC)
    )
    root = await recorder.start_trace("private-slack-thread", "TOKEN=top-secret")
    await recorder.start_step(
        root, "tool", None, "tool", {"password": "nope"}, {"name": "web_search"}
    )
    await recorder.finish_step(root, "tool", "completed", output={"token": "also-nope"})
    await recorder.finish_trace(root, "completed", output="reply")
    await recorder.exporter.flush()

    finished = [span for span in collector.spans if span.ended_at]
    final_root = next(span for span in finished if span.span_id == root.span_id)
    child = next(span for span in finished if span.category == "tool")
    assert final_root.status == "completed"
    assert final_root.content["input"] == "TOKEN=[REDACTED]"
    assert "private-slack-thread" not in str(final_root.attributes)
    assert child.parent_span_id == root.span_id
    assert child.attributes == {
        "span.category": "tool",
        "tool.name": "web_search",
        "turn.status": "completed",
    }
    assert "nope" not in str(child.content)


async def test_export_failure_and_full_queue_do_not_raise_to_the_turn() -> None:
    class FailingExporter:
        async def export(self, spans: list[object]) -> None:
            raise RuntimeError("Phoenix unavailable")

    bounded = BoundedBatchExporter(FailingExporter(), max_queue_size=1)
    recorder = TraceRecorder(bounded)
    root = await recorder.start_trace("thread", "hello")
    await recorder.finish_trace(root, "completed", output="still replies")
    await bounded.flush()

    assert bounded.dropped_events >= 1
    assert bounded.queue_saturation_events >= 1


async def test_alert_policy_notifies_once_after_five_continuous_minutes() -> None:
    clock = datetime(2026, 1, 1, tzinfo=UTC)
    alerts: list[str] = []

    async def notify(message: str) -> None:
        alerts.append(message)

    policy = PersistentAlertPolicy(notify, now=lambda: clock)
    policy.observe("export_failure")
    clock += timedelta(minutes=4, seconds=59)
    policy.observe("export_failure")
    assert alerts == []
    clock += timedelta(seconds=1)
    policy.observe("export_failure")
    await asyncio.sleep(0)
    policy.observe("export_failure")
    await asyncio.sleep(0)
    assert len(alerts) == 1
    policy.recover("export_failure")


def test_otlp_keeps_a_running_span_open_and_marks_failures() -> None:
    started = datetime(2026, 1, 1, tzinfo=UTC)
    running = TelemetrySpan("a" * 32, "b" * 16, None, "turn", "turn", "running", started)
    failed = TelemetrySpan("c" * 32, "d" * 16, None, "tool", "tool", "failed", started, started)

    exported = _otlp_request([running, failed]).resource_spans[0].scope_spans[0].spans

    assert exported[0].end_time_unix_nano == 0
    assert exported[1].status.code == 2

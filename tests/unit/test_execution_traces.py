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
        "openinference.span.kind": "TOOL",
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
    other_root = await recorder.start_trace("other-thread", "hello")
    await recorder.finish_trace(root, "completed", output="still replies")
    await recorder.finish_trace(other_root, "completed", output="still replies")
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


def test_otlp_uses_terminal_status_codes_without_closing_running_spans() -> None:
    started = datetime(2026, 1, 1, tzinfo=UTC)
    running = TelemetrySpan("a" * 32, "b" * 16, None, "turn", "turn", "running", started)
    completed = TelemetrySpan(
        "c" * 32, "d" * 16, None, "tool", "tool", "completed", started, started
    )
    failed = TelemetrySpan("e" * 32, "f" * 16, None, "tool", "tool", "failed", started, started)
    cancelled = TelemetrySpan(
        "1" * 32, "2" * 16, None, "tool", "tool", "cancelled", started, started
    )

    exported = (
        _otlp_request([running, completed, failed, cancelled])
        .resource_spans[0]
        .scope_spans[0]
        .spans
    )

    assert exported[0].end_time_unix_nano == 0
    assert [span.status.code for span in exported] == [0, 1, 2, 0]


async def test_turn_usage_is_aggregated_without_fabricating_missing_model_usage() -> None:
    collector = InMemorySpanExporter()
    recorder = TraceRecorder(BoundedBatchExporter(collector))
    root = await recorder.start_trace("thread", "hello")
    await recorder.start_step(root, "known", None, "model", attributes={"name": "requested"})
    await recorder.finish_step(
        root,
        "known",
        "completed",
        output={
            "response_metadata": {"model_name": "provider-model", "cost_usd": 0.03},
            "usage_metadata": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
        },
    )
    await recorder.start_step(root, "unknown", None, "model", attributes={"name": "requested"})
    await recorder.finish_step(root, "unknown", "completed", output={})
    await recorder.finish_trace(root, "completed")
    await recorder.exporter.flush()

    terminal = [span for span in collector.spans if span.ended_at]
    model = next(span for span in terminal if span.category == "model" and span.ended_at)
    turn = next(span for span in terminal if span.category == "turn")
    assert model.attributes["llm.request.model_name"] == "requested"
    assert model.attributes["llm.response.model_name"] == "provider-model"
    assert model.attributes["llm.model_name"] == "provider-model"
    assert model.attributes["openinference.span.kind"] == "LLM"
    assert model.attributes["llm.token_count.total"] == 5
    assert model.attributes["llm.cost.total"] == 0.03
    assert turn.attributes["turn.token_count.prompt"] == 2
    assert turn.attributes["turn.token_count.completion"] == 3
    assert turn.attributes["turn.token_count.total"] == 5
    assert turn.attributes["turn.model_usage.completeness"] == "partial"


async def test_missing_usage_and_cost_remain_unavailable_with_requested_model_as_fallback() -> None:
    collector = InMemorySpanExporter()
    recorder = TraceRecorder(BoundedBatchExporter(collector))
    root = await recorder.start_trace("thread", "hello")
    await recorder.start_step(root, "model", None, "model", attributes={"name": "requested"})
    await recorder.finish_step(root, "model", "completed", output={})
    await recorder.finish_trace(root, "completed")
    await recorder.exporter.flush()

    terminal = [span for span in collector.spans if span.ended_at]
    model = next(span for span in terminal if span.category == "model")
    turn = next(span for span in terminal if span.category == "turn")
    assert model.attributes["llm.model_name"] == "requested"
    assert "llm.response.model_name" not in model.attributes
    assert "llm.token_count.total" not in model.attributes
    assert "llm.cost.total" not in model.attributes
    assert turn.attributes["turn.model_usage.completeness"] == "unavailable"
    assert "turn.token_count.total" not in turn.attributes

"""Vendor-neutral, best-effort telemetry for Slack Turns.

Phoenix is the trace store and UI. The Assistant only creates safe spans and
hands them to a bounded OTLP boundary, which may drop work under pressure.
"""

import asyncio
import logging
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Protocol

import aiohttp
from google.protobuf.message import Message  # type: ignore[import-untyped]
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.common.v1.common_pb2 import KeyValue

logger = logging.getLogger(__name__)
TraceStatus = str
DEFAULT_PAYLOAD_LIMIT = 8_000
_SECRET_NAME = re.compile(r"(token|secret|password|key|credential)", re.I)
_BEARER = re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.I)
_SLACK_TOKEN = re.compile(r"xox(?:a|b|p|r|s)-[A-Za-z0-9-]+")
_NAMED_SECRET = re.compile(
    r"\b((?=[A-Za-z0-9_]*(?:token|secret|password|key))[A-Za-z_][A-Za-z0-9_]*)=([^\s&]+)", re.I
)

METADATA_ALLOWLIST = frozenset(
    {
        "service.name",
        "service.version",
        "deployment.environment",
        "turn.status",
        "span.category",
        "model.name",
        "tool.name",
        "graph.node",
        "duration.ms",
        "token.input",
        "token.output",
        "cost.usd",
        "retry.count",
        "error.class",
        "error.fingerprint",
        "redaction.count",
        "payload.dropped",
    }
)


@dataclass(frozen=True)
class TelemetrySpan:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    category: str
    status: TraceStatus
    started_at: datetime
    ended_at: datetime | None = None
    attributes: dict[str, object] = field(default_factory=dict)
    content: dict[str, object] = field(default_factory=dict)


class SpanExporter(Protocol):
    async def export(self, spans: list[TelemetrySpan]) -> None: ...


class InMemorySpanExporter:
    """Test collector at the adapter boundary used by OTLP."""

    def __init__(self) -> None:
        self.spans: list[TelemetrySpan] = []

    async def export(self, spans: list[TelemetrySpan]) -> None:
        self.spans.extend(spans)


class OtlpHttpExporter:
    """OTLP/HTTP adapter; a replacement backend changes only this class."""

    def __init__(self, endpoint: str, timeout_seconds: float = 2.0) -> None:
        self.endpoint = endpoint.rstrip("/") + "/v1/traces"
        self.timeout_seconds = timeout_seconds

    async def export(self, spans: list[TelemetrySpan]) -> None:
        payload = _otlp_request(spans).SerializeToString()
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                self.endpoint, data=payload, headers={"Content-Type": "application/x-protobuf"}
            ) as response:
                response.raise_for_status()


class BoundedBatchExporter:
    """A non-blocking bounded queue with structured loss diagnostics."""

    def __init__(
        self,
        exporter: SpanExporter,
        *,
        max_queue_size: int = 512,
        batch_size: int = 64,
        alert_policy: "PersistentAlertPolicy | None" = None,
    ) -> None:
        self.exporter, self.queue = exporter, asyncio.Queue[TelemetrySpan](maxsize=max_queue_size)
        self.batch_size = batch_size
        self.alert_policy = alert_policy
        self.dropped_events = self.export_failures = self.queue_saturation_events = 0
        self._worker: asyncio.Task[None] | None = None

    def submit(self, span: TelemetrySpan) -> None:
        try:
            self.queue.put_nowait(span)
        except asyncio.QueueFull:
            self.dropped_events += 1
            self.queue_saturation_events += 1
            logger.warning("telemetry queue saturated; dropping span", extra={"span": span.name})
            self._observe("queue_saturation")
            return
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._drain())

    async def flush(self) -> None:
        if self._worker is not None:
            await self._worker

    async def _drain(self) -> None:
        while not self.queue.empty():
            batch = [self.queue.get_nowait()]
            while len(batch) < self.batch_size and not self.queue.empty():
                batch.append(self.queue.get_nowait())
            try:
                await self.exporter.export(batch)
            except Exception:
                self.export_failures += len(batch)
                logger.exception("telemetry export failed", extra={"span_count": len(batch)})
                self._observe("export_failure")
            else:
                self._recover("export_failure")

    def _observe(self, signal: str) -> None:
        if self.alert_policy is not None:
            self.alert_policy.observe(signal)

    def _recover(self, signal: str) -> None:
        if self.alert_policy is not None:
            self.alert_policy.recover(signal)


class PersistentAlertPolicy:
    """Alerts once only after a telemetry signal breaches for five minutes."""

    def __init__(
        self, notify: Callable[[str], Awaitable[None]], *, now: Callable[[], datetime] | None = None
    ) -> None:
        self.notify, self.now = notify, now or (lambda: datetime.now(UTC))
        self._started: dict[str, datetime] = {}
        self._alerted: set[str] = set()

    def observe(self, signal: str) -> None:
        started = self._started.setdefault(signal, self.now())
        if signal not in self._alerted and self.now() - started >= timedelta(minutes=5):
            self._alerted.add(signal)
            asyncio.ensure_future(
                self.notify(f"Observability alert: {signal} has persisted for five minutes.")
            )

    def recover(self, signal: str) -> None:
        self._started.pop(signal, None)
        self._alerted.discard(signal)


def safe_value(value: object, *, payload_limit: int = DEFAULT_PAYLOAD_LIMIT) -> object:
    """Redact recursively and limit every exported content value."""
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]"
            if _SECRET_NAME.search(str(key))
            else safe_value(item, payload_limit=payload_limit)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [safe_value(item, payload_limit=payload_limit) for item in value]
    text = _NAMED_SECRET.sub(r"\1=[REDACTED]", str(value))
    return _SLACK_TOKEN.sub("[REDACTED]", _BEARER.sub("Bearer [REDACTED]", text))[:payload_limit]


class TraceRecorder:
    """Correlates one Slack Turn with a root span and safe child spans."""

    def __init__(
        self,
        exporter: BoundedBatchExporter,
        *,
        now: Callable[[], datetime] | None = None,
        payload_limit: int = DEFAULT_PAYLOAD_LIMIT,
    ) -> None:
        self.exporter, self.now, self.payload_limit = (
            exporter,
            now or (lambda: datetime.now(UTC)),
            payload_limit,
        )
        self._spans: dict[tuple[str, str], TelemetrySpan] = {}

    async def start_trace(self, conversation_id: str, input: object) -> TelemetrySpan:
        del conversation_id  # Slack identity is intentionally not metadata.
        root = TelemetrySpan(
            uuid.uuid4().hex,
            uuid.uuid4().hex[:16],
            None,
            "slack.turn",
            "turn",
            "running",
            self.now(),
            attributes={"service.name": "assistant", "span.category": "turn"},
            content={"input": safe_value(input, payload_limit=self.payload_limit)},
        )
        self._spans[(root.trace_id, root.span_id)] = root
        self.exporter.submit(root)
        return root

    async def finish_trace(
        self,
        trace: TelemetrySpan,
        status: TraceStatus,
        *,
        output: object | None = None,
        error: object | None = None,
    ) -> None:
        self._finish(trace, status, output, error)

    async def start_step(
        self,
        trace: TelemetrySpan,
        step_id: str,
        parent_step_id: str | None,
        category: str,
        input: object | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        allowed = _safe_metadata(attributes or {}, category)
        name = str(
            allowed.get("tool.name")
            or allowed.get("model.name")
            or allowed.get("graph.node")
            or category
        )
        parent = self._spans.get((trace.trace_id, parent_step_id or ""))
        span = TelemetrySpan(
            trace.trace_id,
            uuid.uuid4().hex[:16],
            parent.span_id if parent else trace.span_id,
            name,
            category,
            "running",
            self.now(),
            attributes=allowed,
            content={"input": safe_value(input, payload_limit=self.payload_limit)}
            if input is not None
            else {},
        )
        self._spans[(trace.trace_id, step_id)] = span
        self.exporter.submit(span)

    async def finish_step(
        self,
        trace: TelemetrySpan,
        step_id: str,
        status: TraceStatus,
        *,
        output: object | None = None,
        error: object | None = None,
    ) -> None:
        span = self._spans.get((trace.trace_id, step_id))
        if span is not None:
            self._finish(span, status, output, error)

    def _finish(
        self, span: TelemetrySpan, status: TraceStatus, output: object | None, error: object | None
    ) -> None:
        content, attrs = dict(span.content), dict(span.attributes)
        if output is not None:
            content["output"] = safe_value(output, payload_limit=self.payload_limit)
        attrs["turn.status"] = status
        if error is not None:
            attrs["error.class"] = (
                type(error).__name__ if isinstance(error, BaseException) else "error"
            )
        finished = replace(
            span, status=status, ended_at=self.now(), attributes=attrs, content=content
        )
        for key, current in self._spans.items():
            if current is span:
                self._spans[key] = finished
                break
        self.exporter.submit(finished)


def _safe_metadata(attributes: Mapping[str, object], category: str) -> dict[str, object]:
    alias = {
        "name": "tool.name"
        if category == "tool"
        else "model.name"
        if category == "model"
        else "graph.node"
    }
    result: dict[str, object] = {"span.category": category}
    for key, value in attributes.items():
        allowed = alias.get(key, key)
        if allowed in METADATA_ALLOWLIST:
            result[allowed] = safe_value(value)
    return result


def _otlp_request(spans: list[TelemetrySpan]) -> ExportTraceServiceRequest:
    request = ExportTraceServiceRequest()
    scope_spans = request.resource_spans.add().scope_spans.add()
    for span in spans:
        target = scope_spans.spans.add()
        target.trace_id = bytes.fromhex(span.trace_id)
        target.span_id = bytes.fromhex(span.span_id)
        if span.parent_span_id:
            target.parent_span_id = bytes.fromhex(span.parent_span_id)
        target.name = span.name
        target.start_time_unix_nano = int(span.started_at.timestamp() * 1_000_000_000)
        if span.ended_at is not None:
            target.end_time_unix_nano = int(span.ended_at.timestamp() * 1_000_000_000)
        # OpenTelemetry StatusCode: 0 = unset, 2 = error.
        target.status.code = 2 if span.status == "failed" else 0
        _add_otlp_attributes(target, span)
    return request


def _add_otlp_attributes(target: Message, span: TelemetrySpan) -> None:
    attributes = {
        **span.attributes,
        **{f"content.{key}": value for key, value in span.content.items()},
    }
    for key, value in attributes.items():
        attribute = target.attributes.add()
        attribute.CopyFrom(KeyValue(key=key))
        attribute.value.string_value = str(value)

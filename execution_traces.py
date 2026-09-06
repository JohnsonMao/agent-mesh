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
        "turn.token_count.prompt",
        "turn.token_count.completion",
        "turn.token_count.total",
        "turn.model_usage.completeness",
        "span.category",
        "llm.model_name",
        "llm.request.model_name",
        "llm.response.model_name",
        "llm.token_count.prompt",
        "llm.token_count.completion",
        "llm.token_count.total",
        "llm.cost.total",
        "tool.name",
        "graph.node",
        "duration.ms",
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
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump()
            if isinstance(dumped, Mapping) and "content" in dumped:
                return safe_value(dumped["content"], payload_limit=payload_limit)
            return safe_value(dumped, payload_limit=payload_limit)
        except Exception:
            # Telemetry must not make a Slack reply depend on a foreign object's serializer.
            pass
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]"
            if _SECRET_NAME.search(str(key))
            else safe_value(item, payload_limit=payload_limit)
            for key, item in value.items()
            if key != "sent_at"
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
            or allowed.get("llm.request.model_name")
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
            extra_attributes = _model_attributes(output) if span.category == "model" else {}
            self._finish(span, status, output, error, extra_attributes)

    def _finish(
        self,
        span: TelemetrySpan,
        status: TraceStatus,
        output: object | None,
        error: object | None,
        extra_attributes: Mapping[str, object] | None = None,
    ) -> None:
        content, attrs = dict(span.content), dict(span.attributes)
        attrs.update(extra_attributes or {})
        if output is not None:
            content["output"] = safe_value(output, payload_limit=self.payload_limit)
        attrs["turn.status"] = status
        if error is not None:
            attrs["error.class"] = (
                type(error).__name__ if isinstance(error, BaseException) else "error"
            )
        if span.category == "turn":
            attrs.update(self._turn_usage_attributes(span.trace_id))
        finished = replace(
            span, status=status, ended_at=self.now(), attributes=attrs, content=content
        )
        for key, current in self._spans.items():
            if current is span:
                self._spans[key] = finished
                break
        self.exporter.submit(finished)

    def _turn_usage_attributes(self, trace_id: str) -> dict[str, object]:
        model_spans = [
            span
            for (span_trace_id, _), span in self._spans.items()
            if span_trace_id == trace_id and span.category == "model" and span.ended_at is not None
        ]
        known = [
            span
            for span in model_spans
            if any(
                key in span.attributes
                for key in (
                    "llm.token_count.prompt",
                    "llm.token_count.completion",
                    "llm.token_count.total",
                )
            )
        ]
        completeness = (
            "unavailable"
            if not known
            else "complete"
            if len(known) == len(model_spans)
            else "partial"
        )
        result: dict[str, object] = {"turn.model_usage.completeness": completeness}
        if known:
            for source, target in (
                ("llm.token_count.prompt", "turn.token_count.prompt"),
                ("llm.token_count.completion", "turn.token_count.completion"),
                ("llm.token_count.total", "turn.token_count.total"),
            ):
                values = [span.attributes[source] for span in known if source in span.attributes]
                if values:
                    result[target] = sum(
                        value for value in values if isinstance(value, int | float)
                    )
        return result


def _safe_metadata(attributes: Mapping[str, object], category: str) -> dict[str, object]:
    alias = {
        "name": "tool.name"
        if category == "tool"
        else "llm.request.model_name"
        if category == "model"
        else "graph.node"
    }
    result: dict[str, object] = {"span.category": category}
    for key, value in attributes.items():
        allowed = alias.get(key, key)
        if allowed in METADATA_ALLOWLIST:
            result[allowed] = safe_value(value)
            if allowed == "llm.request.model_name":
                # OpenInference displays this primary identity; a response value replaces it at end.
                result["llm.model_name"] = safe_value(value)
    return result


def _model_attributes(output: object | None) -> dict[str, object]:
    """Extract only provider-reported model identity, usage, and cost."""
    response_metadata = getattr(output, "response_metadata", None)
    usage_metadata = getattr(output, "usage_metadata", None)
    if isinstance(output, Mapping):
        response_metadata = output.get("response_metadata", response_metadata)
        usage_metadata = output.get("usage_metadata", usage_metadata)
    response = response_metadata if isinstance(response_metadata, Mapping) else {}
    usage = usage_metadata if isinstance(usage_metadata, Mapping) else {}
    token_usage = response.get("token_usage")
    if isinstance(token_usage, Mapping):
        usage = {**token_usage, **usage}

    result: dict[str, object] = {}
    model = response.get("model_name") or response.get("model")
    if isinstance(model, str) and model:
        result["llm.response.model_name"] = model
        result["llm.model_name"] = model
    for target, names in (
        ("llm.token_count.prompt", ("input_tokens", "prompt_tokens")),
        ("llm.token_count.completion", ("output_tokens", "completion_tokens")),
        ("llm.token_count.total", ("total_tokens",)),
    ):
        value = next(
            (usage[name] for name in names if isinstance(usage.get(name), int | float)), None
        )
        if value is not None:
            result[target] = value
    cost = next(
        (
            response[name]
            for name in ("cost_usd", "cost", "total_cost")
            if isinstance(response.get(name), int | float)
        ),
        None,
    )
    if cost is not None:
        result["llm.cost.total"] = cost
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
        # OpenTelemetry StatusCode: 0 = unset, 1 = OK, 2 = error.
        target.status.code = (
            2 if span.status == "failed" else 1 if span.status == "completed" else 0
        )
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
        if isinstance(value, bool):
            attribute.value.bool_value = value
        elif isinstance(value, int):
            attribute.value.int_value = value
        elif isinstance(value, float):
            attribute.value.double_value = value
        else:
            attribute.value.string_value = str(value)

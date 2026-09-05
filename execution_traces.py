"""Best-effort, OpenTelemetry-shaped execution traces and their local viewer."""

import html
import json
import logging
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast

from aiohttp import web
from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)

TraceStatus = str
CONTENT_RETENTION = timedelta(days=14)
METADATA_RETENTION = timedelta(days=90)
DEFAULT_COMMAND_OUTPUT_LIMIT = 8_000
_SECRET_NAME = re.compile(r"(token|secret|password|key)", re.IGNORECASE)
_BEARER = re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_SLACK_TOKEN = re.compile(r"xox(?:a|b|p|r|s)-[A-Za-z0-9-]+")
_NAMED_SECRET = re.compile(
    r"\b((?=[A-Za-z0-9_]*(?:token|secret|password|key))[A-Za-z_][A-Za-z0-9_]*)=([^\s&]+)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ExecutionStep:
    step_id: str
    parent_step_id: str | None
    category: str
    status: TraceStatus
    started_at: datetime
    ended_at: datetime | None = None
    input: object | None = None
    output: object | None = None
    attributes: dict[str, object] = field(default_factory=dict)
    error: str | None = None

    @property
    def duration_seconds(self) -> float | None:
        if self.ended_at is None:
            return None
        return (self.ended_at - self.started_at).total_seconds()


@dataclass(frozen=True)
class ExecutionTrace:
    trace_id: str
    conversation_id: str
    status: TraceStatus
    started_at: datetime
    ended_at: datetime | None = None
    input: object | None = None
    output: object | None = None
    attributes: dict[str, object] = field(default_factory=dict)
    error: str | None = None
    steps: tuple[ExecutionStep, ...] = ()


class TraceRepository(Protocol):
    async def create_trace(self, trace: ExecutionTrace) -> None: ...

    async def update_trace(self, trace: ExecutionTrace) -> None: ...

    async def create_step(self, trace_id: str, step: ExecutionStep) -> None: ...

    async def update_step(self, trace_id: str, step: ExecutionStep) -> None: ...

    async def list_traces(
        self,
        *,
        status: str | None = None,
        conversation_id: str | None = None,
        started_after: datetime | None = None,
        started_before: datetime | None = None,
    ) -> list[ExecutionTrace]: ...

    async def get_trace(self, trace_id: str) -> ExecutionTrace | None: ...

    async def cleanup(self, content_before: datetime, metadata_before: datetime) -> None: ...


class InMemoryTraceRepository:
    """A repository implementation for unit tests and local behavior checks."""

    def __init__(self) -> None:
        self.traces: dict[str, ExecutionTrace] = {}

    async def create_trace(self, trace: ExecutionTrace) -> None:
        self.traces[trace.trace_id] = trace

    async def update_trace(self, trace: ExecutionTrace) -> None:
        self.traces[trace.trace_id] = replace(trace, steps=self.traces[trace.trace_id].steps)

    async def create_step(self, trace_id: str, step: ExecutionStep) -> None:
        trace = self.traces[trace_id]
        self.traces[trace_id] = replace(trace, steps=(*trace.steps, step))

    async def update_step(self, trace_id: str, step: ExecutionStep) -> None:
        trace = self.traces[trace_id]
        self.traces[trace_id] = replace(
            trace,
            steps=tuple(step if item.step_id == step.step_id else item for item in trace.steps),
        )

    async def list_traces(
        self,
        *,
        status: str | None = None,
        conversation_id: str | None = None,
        started_after: datetime | None = None,
        started_before: datetime | None = None,
    ) -> list[ExecutionTrace]:
        traces = list(self.traces.values())
        return sorted(
            (
                trace
                for trace in traces
                if (not status or trace.status == status)
                and (not conversation_id or trace.conversation_id == conversation_id)
                and (not started_after or trace.started_at >= started_after)
                and (not started_before or trace.started_at <= started_before)
            ),
            key=lambda trace: trace.started_at,
            reverse=True,
        )

    async def get_trace(self, trace_id: str) -> ExecutionTrace | None:
        return self.traces.get(trace_id)

    async def cleanup(self, content_before: datetime, metadata_before: datetime) -> None:
        for trace_id, trace in tuple(self.traces.items()):
            if trace.ended_at and trace.ended_at < metadata_before:
                del self.traces[trace_id]
            elif trace.ended_at and trace.ended_at < content_before:
                self.traces[trace_id] = replace(
                    trace,
                    input=None,
                    output=None,
                    error=None,
                    steps=tuple(
                        replace(step, input=None, output=None, error=None) for step in trace.steps
                    ),
                )


class NullTraceRepository:
    """Keeps trace capture non-fatal when PostgreSQL is temporarily unavailable."""

    async def create_trace(self, trace: ExecutionTrace) -> None:
        return None

    async def update_trace(self, trace: ExecutionTrace) -> None:
        return None

    async def create_step(self, trace_id: str, step: ExecutionStep) -> None:
        return None

    async def update_step(self, trace_id: str, step: ExecutionStep) -> None:
        return None

    async def list_traces(
        self,
        *,
        status: str | None = None,
        conversation_id: str | None = None,
        started_after: datetime | None = None,
        started_before: datetime | None = None,
    ) -> list[ExecutionTrace]:
        return []

    async def get_trace(self, trace_id: str) -> ExecutionTrace | None:
        return None

    async def cleanup(self, content_before: datetime, metadata_before: datetime) -> None:
        return None


class PostgresTraceRepository:
    """PostgreSQL storage. Setup is intentionally idempotent for this small app."""

    def __init__(self, pool: AsyncConnectionPool[AsyncConnection[DictRow]]) -> None:
        self.pool = pool

    async def setup(self) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                """CREATE TABLE IF NOT EXISTS execution_traces (
                    trace_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, status TEXT NOT NULL,
                    started_at TIMESTAMPTZ NOT NULL, ended_at TIMESTAMPTZ,
                    input JSONB, output JSONB, attributes JSONB NOT NULL DEFAULT '{}', error TEXT
                )"""
            )
            await conn.execute(
                """CREATE TABLE IF NOT EXISTS execution_steps (
                    trace_id TEXT NOT NULL REFERENCES execution_traces(trace_id) ON DELETE CASCADE,
                    step_id TEXT NOT NULL, parent_step_id TEXT, category TEXT NOT NULL, status TEXT NOT NULL,
                    started_at TIMESTAMPTZ NOT NULL, ended_at TIMESTAMPTZ,
                    input JSONB, output JSONB, attributes JSONB NOT NULL DEFAULT '{}', error TEXT,
                    PRIMARY KEY(trace_id, step_id)
                )"""
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS execution_traces_list_idx "
                "ON execution_traces (started_at DESC, status, conversation_id)"
            )

    async def create_trace(self, trace: ExecutionTrace) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO execution_traces VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                _trace_values(trace),
            )

    async def update_trace(self, trace: ExecutionTrace) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                """UPDATE execution_traces SET status=%s, ended_at=%s, input=%s, output=%s,
                   attributes=%s, error=%s WHERE trace_id=%s""",
                (
                    trace.status,
                    trace.ended_at,
                    _json(trace.input),
                    _json(trace.output),
                    _json(trace.attributes),
                    trace.error,
                    trace.trace_id,
                ),
            )

    async def create_step(self, trace_id: str, step: ExecutionStep) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO execution_steps VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (trace_id, *_step_values(step)),
            )

    async def update_step(self, trace_id: str, step: ExecutionStep) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                """UPDATE execution_steps SET parent_step_id=%s, category=%s, status=%s,
                   started_at=%s, ended_at=%s, input=%s, output=%s, attributes=%s, error=%s
                   WHERE trace_id=%s AND step_id=%s""",
                (*_step_values(step)[1:], trace_id, step.step_id),
            )

    async def list_traces(self, **filters: object) -> list[ExecutionTrace]:
        clauses, params = [], []
        for column, filter_name, operator in (
            ("status", "status", "="),
            ("conversation_id", "conversation_id", "="),
            ("started_at", "started_after", ">="),
            ("started_at", "started_before", "<="),
        ):
            if value := filters.get(filter_name):
                clauses.append(f"{column} {operator} %s")
                params.append(value)
        query = "SELECT * FROM execution_traces"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY started_at DESC"
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(query, params)).fetchall()
        return [_trace_from_row(cast(Mapping[str, object], row)) for row in rows]

    async def get_trace(self, trace_id: str) -> ExecutionTrace | None:
        async with self.pool.connection() as conn:
            trace_row = await (
                await conn.execute("SELECT * FROM execution_traces WHERE trace_id=%s", (trace_id,))
            ).fetchone()
            if trace_row is None:
                return None
            step_rows = await (
                await conn.execute(
                    "SELECT * FROM execution_steps WHERE trace_id=%s ORDER BY started_at, step_id",
                    (trace_id,),
                )
            ).fetchall()
        return replace(
            _trace_from_row(cast(Mapping[str, object], trace_row)),
            steps=tuple(_step_from_row(cast(Mapping[str, object], row)) for row in step_rows),
        )

    async def cleanup(self, content_before: datetime, metadata_before: datetime) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "DELETE FROM execution_traces WHERE ended_at < %s", (metadata_before,)
            )
            await conn.execute(
                "UPDATE execution_traces SET input=NULL, output=NULL, error=NULL WHERE ended_at < %s",
                (content_before,),
            )
            await conn.execute(
                "UPDATE execution_steps SET input=NULL, output=NULL, error=NULL WHERE ended_at < %s",
                (content_before,),
            )


def _json(value: object | None) -> str | None:
    return None if value is None else json.dumps(value, default=str)


def _trace_values(trace: ExecutionTrace) -> tuple[object, ...]:
    return (
        trace.trace_id,
        trace.conversation_id,
        trace.status,
        trace.started_at,
        trace.ended_at,
        _json(trace.input),
        _json(trace.output),
        _json(trace.attributes),
        trace.error,
    )


def _step_values(step: ExecutionStep) -> tuple[object, ...]:
    return (
        step.step_id,
        step.parent_step_id,
        step.category,
        step.status,
        step.started_at,
        step.ended_at,
        _json(step.input),
        _json(step.output),
        _json(step.attributes),
        step.error,
    )


def _trace_from_row(row: Mapping[str, object]) -> ExecutionTrace:
    return ExecutionTrace(**dict(row), steps=())  # type: ignore[arg-type]


def _step_from_row(row: Mapping[str, object]) -> ExecutionStep:
    return ExecutionStep(**dict(row))  # type: ignore[arg-type]


def safe_value(
    value: object, *, command_output_limit: int = DEFAULT_COMMAND_OUTPUT_LIMIT
) -> object:
    """Recursively redact common secrets before a value reaches persistent storage."""
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]"
            if _SECRET_NAME.search(str(key))
            else safe_value(item, command_output_limit=command_output_limit)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [safe_value(item, command_output_limit=command_output_limit) for item in value]
    text = str(value)
    text = _NAMED_SECRET.sub(r"\1=[REDACTED]", text)
    text = _SLACK_TOKEN.sub("[REDACTED]", _BEARER.sub("Bearer [REDACTED]", text))
    return text


class TraceRecorder:
    """Turns repository errors into diagnostic logs so tracing never blocks Slack."""

    def __init__(
        self,
        repository: TraceRepository,
        *,
        now: Callable[[], datetime] | None = None,
        command_output_limit: int = DEFAULT_COMMAND_OUTPUT_LIMIT,
    ) -> None:
        self.repository = repository
        self.now = now or (lambda: datetime.now(UTC))
        self.command_output_limit = command_output_limit
        self._traces: dict[str, ExecutionTrace] = {}
        self._steps: dict[tuple[str, str], ExecutionStep] = {}

    async def start_trace(self, conversation_id: str, input: object) -> ExecutionTrace:
        trace = ExecutionTrace(
            uuid.uuid4().hex, conversation_id, "running", self.now(), input=safe_value(input)
        )
        self._traces[trace.trace_id] = trace
        await self._best_effort("creating execution trace", self.repository.create_trace(trace))
        return trace

    async def finish_trace(
        self,
        trace: ExecutionTrace,
        status: TraceStatus,
        *,
        output: object | None = None,
        error: object | None = None,
    ) -> None:
        current = self._traces.get(trace.trace_id, trace)
        finished = replace(
            current,
            status=status,
            ended_at=self.now(),
            output=safe_value(output) if output is not None else None,
            error=str(safe_value(error)) if error else None,
        )
        self._traces[trace.trace_id] = finished
        await self._best_effort(
            "finalizing execution trace", self.repository.update_trace(finished)
        )
        await self._best_effort(
            "cleaning execution traces",
            self.repository.cleanup(
                self.now() - CONTENT_RETENTION, self.now() - METADATA_RETENTION
            ),
        )

    async def start_step(
        self,
        trace: ExecutionTrace,
        step_id: str,
        parent_step_id: str | None,
        category: str,
        input: object | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        safe_attributes = safe_value(attributes or {})
        assert isinstance(safe_attributes, Mapping)
        step = ExecutionStep(
            step_id,
            parent_step_id,
            category,
            "running",
            self.now(),
            input=safe_value(input) if input is not None else None,
            attributes=dict(safe_attributes),
        )
        self._steps[(trace.trace_id, step_id)] = step
        await self._best_effort(
            "creating execution step", self.repository.create_step(trace.trace_id, step)
        )

    async def finish_step(
        self,
        trace: ExecutionTrace,
        step_id: str,
        status: TraceStatus,
        *,
        output: object | None = None,
        error: object | None = None,
    ) -> None:
        previous = self._steps.get((trace.trace_id, step_id))
        if previous is None:
            return
        if (
            previous.category == "tool"
            and previous.attributes.get("name") == "execute_command"
            and output is not None
        ):
            output = str(output)[: self.command_output_limit]
        step = replace(
            previous,
            status=status,
            ended_at=self.now(),
            output=safe_value(output) if output is not None else None,
            error=str(safe_value(error)) if error else None,
        )
        self._steps[(trace.trace_id, step_id)] = step
        await self._best_effort(
            "finalizing execution step", self.repository.update_step(trace.trace_id, step)
        )

    async def _best_effort(self, operation: str, awaitable: Awaitable[None]) -> None:
        try:
            await awaitable
        except Exception:
            logger.exception("Failed while %s", operation)

    async def cleanup(self, now: datetime | None = None) -> None:
        """Run retention from the terminal-Turn hook or a controllable test clock."""
        moment = now or self.now()
        await self._best_effort(
            "cleaning execution traces",
            self.repository.cleanup(moment - CONTENT_RETENTION, moment - METADATA_RETENTION),
        )


def create_viewer_app(repository: TraceRepository) -> web.Application:
    app = web.Application()

    async def list_view(request: web.Request) -> web.Response:
        try:
            traces = await repository.list_traces(
                status=request.query.get("status"),
                conversation_id=request.query.get("conversation_id"),
                started_after=_parse_time(request.query.get("started_after")),
                started_before=_parse_time(request.query.get("started_before")),
            )
            return web.Response(text=_list_html(traces, request.query), content_type="text/html")
        except Exception:
            logger.exception("Execution trace viewer list failed")
            return web.Response(
                status=503, text="Execution trace viewer is temporarily unavailable"
            )

    async def detail_view(request: web.Request) -> web.Response:
        try:
            trace = await repository.get_trace(request.match_info["trace_id"])
            if trace is None:
                raise web.HTTPNotFound()
            return web.Response(text=_detail_html(trace), content_type="text/html")
        except web.HTTPException:
            raise
        except Exception:
            logger.exception("Execution trace viewer detail failed")
            return web.Response(
                status=503, text="Execution trace viewer is temporarily unavailable"
            )

    app.router.add_get("/", list_view)
    app.router.add_get("/traces", list_view)
    app.router.add_get("/traces/{trace_id}", detail_view)
    return app


def _parse_time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def _display(value: object | None) -> str:
    return "—" if value is None else html.escape(json.dumps(value, indent=2, default=str))


def _list_html(traces: list[ExecutionTrace], query: Mapping[str, str]) -> str:
    rows = "".join(
        f'<tr><td><a href="/traces/{html.escape(trace.trace_id)}">{html.escape(trace.trace_id)}</a></td><td>{html.escape(trace.status)}</td><td>{html.escape(trace.conversation_id)}</td><td>{trace.started_at.isoformat()}</td></tr>'
        for trace in traces
    )

    def field(name: str) -> str:
        return html.escape(query.get(name, ""))

    return f'''<!doctype html><title>Execution traces</title><h1>Execution traces</h1>
<form action="/traces"><label>Status <select name="status"><option value="">all</option>{"".join(f'<option value="{status}"{" selected" if field("status") == status else ""}>{status}</option>' for status in ("completed", "failed", "cancelled"))}</select></label>
<label>Conversation <input name="conversation_id" value="{field("conversation_id")}"></label>
<label>After <input name="started_after" value="{field("started_after")}"></label>
<label>Before <input name="started_before" value="{field("started_before")}"></label><button>Filter</button></form>
<p>Refreshes every 5 seconds.</p><table><tr><th>Trace</th><th>Status</th><th>Conversation</th><th>Started</th></tr>{rows}</table><script>setInterval(() => location.reload(), 5000)</script>'''


def _detail_html(trace: ExecutionTrace) -> str:
    def step_html(step: ExecutionStep) -> str:
        duration = (
            f"{step.duration_seconds:.3f}s" if step.duration_seconds is not None else "in progress"
        )
        return f"<section><h2>{html.escape(step.step_id)} · {html.escape(step.category)} · {html.escape(step.status)}</h2><p>parent: {html.escape(step.parent_step_id or '—')} · started: {step.started_at.isoformat()} · ended: {step.ended_at.isoformat() if step.ended_at else '—'} · duration: {duration}</p><h3>Attributes</h3><pre>{_display(step.attributes)}</pre><h3>Input</h3><pre>{_display(step.input)}</pre><h3>Output</h3><pre>{_display(step.output)}</pre><h3>Error</h3><pre>{_display(step.error)}</pre></section>"

    steps = "".join(step_html(step) for step in trace.steps)
    return f'<!doctype html><title>{html.escape(trace.trace_id)}</title><a href="/traces">← traces</a><h1>{html.escape(trace.trace_id)}</h1><p>{html.escape(trace.status)} · {html.escape(trace.conversation_id)} · started: {trace.started_at.isoformat()} · ended: {trace.ended_at.isoformat() if trace.ended_at else "—"}</p><h2>Attributes</h2><pre>{_display(trace.attributes)}</pre><h2>Input</h2><pre>{_display(trace.input)}</pre><h2>Output</h2><pre>{_display(trace.output)}</pre><h2>Error</h2><pre>{_display(trace.error)}</pre>{steps}'

"""Turn a compiled LangGraph run into typed trace events, in order, as the run happens.

The demo's trace panel reads these over Server-Sent Events. The events carry what a person needs to follow the run (which node
started and finished, how long it took, which tools were called, what failed) and nothing else: node inputs and outputs are not
copied in, only their key names, because state can hold post text and other data that has no business in a log stream.

A node reports tool use by returning a `tool_calls` list of {"name": ..., "summary": ...} in its update. The graph state must
declare a `tool_calls` key: LangGraph silently drops update keys the state schema does not have.
"""

from __future__ import annotations

import time
from contextlib import aclosing
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable, Literal

from pydantic import BaseModel, ConfigDict

from ..config import TraceSettings

TASKS = "tasks"
TOOL_CALLS_KEY = "tool_calls"
ERROR_MESSAGE_KEY = "error"

Kind = Literal["node_started", "node_finished", "tool_call", "error", "run_finished"]


class TraceEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    seq: int                                  # 1, 2, 3 ... within a run; a gap means an event was lost
    job: str
    kind: Kind
    node: str | None = None
    at: datetime
    elapsed_ms: int | None = None             # node_finished only: how long the node ran
    data: dict[str, Any] = {}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _clip(text: object, limit: int) -> str:
    value = str(text)
    return value if len(value) <= limit else value[:limit]


async def trace_events(
    graph: Any,
    inputs: dict,
    job: str,
    settings: TraceSettings,
    now: Callable[[], datetime] = _utc_now,
    monotonic: Callable[[], float] = time.monotonic,
) -> AsyncIterator[TraceEvent]:
    """Run `graph` on `inputs` and yield one event per node start, node finish, tool call and failure, then run_finished.

    Closing this generator (a client that went away) cancels the graph run underneath it.
    """
    seq = 0
    started: dict[str, tuple[str, float]] = {}      # task id -> (node, start time)
    failed_node: str | None = None

    def event(kind: Kind, node: str | None = None, elapsed_ms: int | None = None, **data: Any) -> TraceEvent:
        nonlocal seq
        seq += 1
        return TraceEvent(seq=seq, job=job, kind=kind, node=node, at=now(), elapsed_ms=elapsed_ms, data=data)

    try:
        async with aclosing(graph.astream(inputs, stream_mode=[TASKS])) as stream:
            async for _mode, task in stream:
                node, task_id = task["name"], task["id"]
                if "input" in task:                                   # the task is starting
                    started[task_id] = (node, monotonic())
                    yield event("node_started", node, inputs=sorted(task["input"]))
                    continue
                _, began = started.pop(task_id, (node, monotonic()))
                elapsed = int((monotonic() - began) * 1000)
                if task.get("error"):
                    failed_node = node          # its text is the exception message, which is not streamed; the raised exception names the class
                    continue
                result = task.get("result") or {}
                for call in result.get(TOOL_CALLS_KEY, []) if isinstance(result, dict) else []:
                    yield event("tool_call", node, name=_clip(call.get("name", ""), settings.summary_max_chars),
                                summary=_clip(call.get("summary", ""), settings.summary_max_chars))
                yield event("node_finished", node, elapsed, outputs=sorted(result) if isinstance(result, dict) else [])
    except Exception as exc:                                          # a node raised: report the class, never the message
        pending = [n for n, _ in started.values()]
        yield event("error", failed_node or (pending[0] if len(pending) == 1 else None), type=type(exc).__name__)
        return
    if failed_node:                                                   # a node failed but the run ended without raising: never report ok
        yield event("error", failed_node, type="NodeFailed")
        return
    yield event("run_finished", status="ok")


def format_sse(event: TraceEvent) -> str:
    """One Server-Sent Event: the sequence number is the SSE id, so a reconnecting client can say where it stopped."""
    return f"id: {event.seq}\nevent: {event.kind}\ndata: {event.model_dump_json()}\n\n"

"""LangGraph run -> trace events -> Server-Sent Events, over a real local HTTP connection (uvicorn in a thread).

The graph is a plain test graph with the real agent's node names. Each node waits 0.2 s, so "the first event arrives before the
last node finishes" can only be true if events are sent as they happen, not after the run ends.
"""

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import TypedDict

import httpx
import pytest
import uvicorn
from langgraph.graph import END, START, StateGraph

from satyasutra import __version__
from satyasutra.agent import TraceEvent, format_sse, trace_events
from satyasutra.api.app import create_app
from satyasutra.config import load_config
from satyasutra.ledger import SQLiteLedgerStore

CONFIG = Path(__file__).resolve().parents[2] / "config"
NODE_DELAY_S = 0.2
NODES = ("planner", "tools", "fuse")
SETTLE_S = 3.0


class State(TypedDict, total=False):
    job: str
    notes: list
    tool_calls: list


def build_graph(log, fail_in=None, tool_summary="searched the Fact Check API"):
    """planner -> tools -> fuse. `log` records when each node finished (monotonic), so tests can compare with event arrival."""
    def make(name):
        async def node(state):
            await asyncio.sleep(NODE_DELAY_S)
            if name == fail_in:
                raise RuntimeError("secret detail that must not be streamed")
            log.append((name, time.monotonic()))
            update = {"notes": [name]}
            if name == "tools":
                update["tool_calls"] = [{"name": "factcheck", "summary": tool_summary}]
            return update
        return node

    g = StateGraph(State)
    for name in NODES:
        g.add_node(name, make(name))
    g.add_edge(START, "planner")
    g.add_edge("planner", "tools")
    g.add_edge("tools", "fuse")
    g.add_edge("fuse", END)
    return g.compile()


class LiveServer:
    def __init__(self, app):
        self.app = app
        self.loop = None
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
        self._ready = threading.Event()

    def _run(self):
        async def main():
            self.loop = asyncio.get_running_loop()
            task = asyncio.create_task(self.server.serve())
            while not self.server.started:
                await asyncio.sleep(0.01)
            self._ready.set()
            await task
        asyncio.run(main())

    def __enter__(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        assert self._ready.wait(10), "the server did not start"
        port = self.server.servers[0].sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(10)

    def task_count(self) -> int:
        async def count():
            return len([t for t in asyncio.all_tasks() if not t.done()])
        return asyncio.run_coroutine_threadsafe(count(), self.loop).result(5)


def make_app(tmp_path, graph):
    return create_app(load_config(CONFIG), SQLiteLedgerStore(tmp_path / "l.sqlite3", code_version=__version__), trace_graph=graph)


def read_events(response):
    """Yield (arrival time, event dict) as each SSE block arrives."""
    block = []
    for line in response.iter_lines():
        if line:
            block.append(line)
            continue
        fields = dict(l.split(": ", 1) for l in block)
        block = []
        yield time.monotonic(), fields


def wait_until(predicate, timeout=SETTLE_S):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


# the endpoint --------------------------------------------------------------------------------------------------------

def test_events_arrive_while_the_run_is_still_going_not_after_it_ends(tmp_path):
    log = []
    with LiveServer(make_app(tmp_path, build_graph(log))) as srv, httpx.Client(timeout=10) as http:
        sent = time.monotonic()
        with http.stream("GET", f"{srv.url}/trace/stream", params={"job": "j1"}) as response:
            assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
            arrivals = list(read_events(response))
    first_arrival = arrivals[0][0]
    finished = dict(log)
    assert first_arrival < finished["fuse"], "the first event must arrive before the last node has finished"
    assert first_arrival < finished["tools"]
    assert first_arrival - sent < NODE_DELAY_S, "node_started for the first node should not wait for any node to finish"
    assert arrivals[-1][0] >= finished["fuse"]


def test_events_are_typed_ordered_and_numbered_from_one(tmp_path):
    log = []
    with LiveServer(make_app(tmp_path, build_graph(log))) as srv, httpx.Client(timeout=10) as http:
        with http.stream("GET", f"{srv.url}/trace/stream", params={"job": "j2"}) as response:
            events = [fields for _, fields in read_events(response)]
    kinds = [(e["event"], json.loads(e["data"]).get("node")) for e in events]
    assert kinds == [
        ("node_started", "planner"), ("node_finished", "planner"),
        ("node_started", "tools"), ("tool_call", "tools"), ("node_finished", "tools"),
        ("node_started", "fuse"), ("node_finished", "fuse"), ("run_finished", None),
    ]
    parsed = [TraceEvent.model_validate_json(e["data"]) for e in events]
    assert [p.seq for p in parsed] == list(range(1, len(parsed) + 1)) and [e["id"] for e in events] == [str(p.seq) for p in parsed]
    assert {p.job for p in parsed} == {"j2"}
    finish = next(p for p in parsed if p.kind == "node_finished" and p.node == "planner")
    assert finish.elapsed_ms >= int(NODE_DELAY_S * 1000) - 20 and finish.data["outputs"] == ["notes"]
    call = next(p for p in parsed if p.kind == "tool_call")
    assert call.data == {"name": "factcheck", "summary": "searched the Fact Check API"}


def test_a_failing_node_ends_the_stream_with_an_error_that_carries_no_message(tmp_path):
    log = []
    with LiveServer(make_app(tmp_path, build_graph(log, fail_in="tools"))) as srv, httpx.Client(timeout=10) as http:
        with http.stream("GET", f"{srv.url}/trace/stream", params={"job": "j3"}) as response:
            raw = "".join(line + "\n" for line in response.iter_lines())
    assert "event: error" in raw and "RuntimeError" in raw and '"node":"tools"' in raw.split("event: error")[1]
    assert "secret detail" not in raw and "run_finished" not in raw


def test_leaving_mid_run_stops_the_run_and_leaves_no_task_or_open_stream_behind(tmp_path):
    log = []
    with LiveServer(make_app(tmp_path, build_graph(log))) as srv:
        before = srv.task_count()
        with httpx.Client(timeout=10) as http, http.stream("GET", f"{srv.url}/trace/stream", params={"job": "j4"}) as response:
            first = next(read_events(response))
            assert first[1]["event"] == "node_started" and srv.app.state.active_traces == 1
        # the client is gone; the planner may finish but the run must not carry on to the last node
        assert wait_until(lambda: srv.app.state.active_traces == 0), "the stream generator was not closed after the client left"
        time.sleep(NODE_DELAY_S * len(NODES) + 0.2)
        assert "fuse" not in dict(log) and "tools" not in dict(log)
        assert wait_until(lambda: srv.task_count() <= before), f"tasks left behind: {srv.task_count()} vs {before} before"


def test_a_bad_job_id_is_422_and_no_graph_is_503(tmp_path):
    with LiveServer(make_app(tmp_path, build_graph([]))) as srv, httpx.Client(timeout=10) as http:
        assert http.get(f"{srv.url}/trace/stream", params={"job": "bad id!"}).status_code == 422
        assert http.get(f"{srv.url}/trace/stream").status_code == 422
    with LiveServer(make_app(tmp_path, None)) as srv, httpx.Client(timeout=10) as http:
        assert http.get(f"{srv.url}/trace/stream", params={"job": "ok"}).status_code == 503


# the event stream on its own -----------------------------------------------------------------------------------------

def test_a_long_tool_summary_is_cut_to_the_configured_length():
    settings = load_config(CONFIG).runtime.trace

    async def collect():
        return [e async for e in trace_events(build_graph([], tool_summary="x" * 1000), {"job": "j"}, "j", settings)]

    call = next(e for e in asyncio.run(collect()) if e.kind == "tool_call")
    assert len(call.data["summary"]) == settings.summary_max_chars


def test_format_sse_is_a_valid_event_block():
    event = TraceEvent(seq=7, job="j", kind="run_finished", at="2026-10-31T00:00:00Z", data={"status": "ok"})
    block = format_sse(event)
    assert block.startswith("id: 7\nevent: run_finished\ndata: {") and block.endswith("\n\n") and block.count("\n") == 4


def test_a_task_error_with_no_exception_is_never_reported_as_ok():
    class Quiet:
        async def astream(self, inputs, stream_mode):
            yield "tasks", {"id": "1", "name": "tools", "input": {"job": "j"}, "triggers": ()}
            yield "tasks", {"id": "1", "name": "tools", "error": "details that stay out", "result": None, "interrupts": []}

    async def collect():
        return [e async for e in trace_events(Quiet(), {"job": "j"}, "j", load_config(CONFIG).runtime.trace)]

    events = asyncio.run(collect())
    assert [e.kind for e in events] == ["node_started", "error"] and events[-1].node == "tools" and "details" not in events[-1].model_dump_json()

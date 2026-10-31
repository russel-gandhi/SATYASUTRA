"""In-memory call sessions for Scam Guard (package SG2). Nothing is written anywhere: a session is a Python object that expires."""

from __future__ import annotations

import time
from threading import Lock

from .script import Call, ScamScript


class SessionFull(Exception):
    """Too many calls are being listened to at once; the caller is told, nothing is dropped silently."""


class Sessions:
    def __init__(self, rules: ScamScript):
        self.rules, self._calls, self._lock = rules, {}, Lock()

    def _sweep(self, now: float) -> None:
        stale = [k for k, (_, seen) in self._calls.items() if now - seen > self.rules.settings.session_s]
        for k in stale:
            del self._calls[k]

    def get(self, session_id: str, now: float | None = None) -> Call:
        now = time.monotonic() if now is None else now
        with self._lock:
            self._sweep(now)
            if session_id not in self._calls and len(self._calls) >= self.rules.settings.max_sessions:
                raise SessionFull()
            call = self._calls.get(session_id, (Call(self.rules), now))[0]
            self._calls[session_id] = (call, now)
            return call

    def end(self, session_id: str) -> None:
        with self._lock:
            self._calls.pop(session_id, None)

    def __len__(self) -> int:
        return len(self._calls)

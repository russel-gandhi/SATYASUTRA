"""Spacing between calls to one rate-limited service."""

from __future__ import annotations

import threading
from typing import Callable

SECONDS_PER_MINUTE = 60


class Spacer:
    """Keeps calls at least 60/limit seconds apart, so the configured per-minute limit is never exceeded."""

    def __init__(self, per_minute: int | None, clock: Callable[[], float], sleep: Callable[[float], None]):
        self._gap = SECONDS_PER_MINUTE / per_minute if per_minute else 0.0
        self._clock, self._sleep, self._next, self._lock = clock, sleep, 0.0, threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = self._clock()
            if now < self._next:
                self._sleep(self._next - now)
                now = self._next
            self._next = now + self._gap

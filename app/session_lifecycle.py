from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable


class SessionLifecycle:
    def __init__(
        self,
        exit_fn: Callable[[int], None] = os._exit,
        shutdown_delay_seconds: float = 3.0,
        stale_seconds: float = 120.0,
    ) -> None:
        self._exit_fn = exit_fn
        self._shutdown_delay = shutdown_delay_seconds
        self._stale_seconds = stale_seconds
        self._sessions: dict[str, float] = {}
        self._shutdown_at: float | None = None
        self._lock = threading.Lock()
        self._started = False

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
        threading.Thread(target=self._watch, name="browser-session-watchdog", daemon=True).start()

    def heartbeat(self, session_id: str, now: float | None = None) -> None:
        moment = time.monotonic() if now is None else now
        with self._lock:
            self._sessions[session_id] = moment
            self._shutdown_at = None

    def leave(self, session_id: str, now: float | None = None) -> None:
        moment = time.monotonic() if now is None else now
        with self._lock:
            self._sessions.pop(session_id, None)
            if not self._sessions:
                self._shutdown_at = moment + self._shutdown_delay

    def check(self, now: float | None = None) -> bool:
        moment = time.monotonic() if now is None else now
        with self._lock:
            stale = [key for key, last_seen in self._sessions.items() if moment - last_seen > self._stale_seconds]
            for key in stale:
                self._sessions.pop(key, None)
            if stale and not self._sessions and self._shutdown_at is None:
                self._shutdown_at = moment + self._shutdown_delay
            return self._shutdown_at is not None and moment >= self._shutdown_at and not self._sessions

    def _watch(self) -> None:
        while True:
            time.sleep(0.5)
            if self.check():
                self._exit_fn(0)


lifecycle = SessionLifecycle()

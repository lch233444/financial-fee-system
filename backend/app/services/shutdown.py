from __future__ import annotations

import signal
import threading
from collections.abc import Callable
from functools import lru_cache

from .codex_app_server import get_codex_app_server


ServerStopHook = Callable[[], None]


def _signal_running_server() -> None:
    """Ask a CLI-started Uvicorn server to run its normal shutdown path."""

    signal.raise_signal(signal.SIGINT)


class ShutdownCoordinator:
    """Coordinate a single, process-wide graceful shutdown request.

    The HTTP route only marks the request and schedules ``execute`` as a
    response background task.  This ensures the browser receives the success
    body before Codex is stopped and the Uvicorn server begins shutting down.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requested = False
        self._executed = False
        self._server_stop_hook: ServerStopHook | None = None

    def register_server_stop_hook(self, hook: ServerStopHook | None) -> None:
        """Register the packaged launcher's direct Uvicorn stop hook."""

        with self._lock:
            self._server_stop_hook = hook

    def request(self) -> bool:
        """Mark shutdown requested, returning ``True`` only for the first call."""

        with self._lock:
            if self._requested:
                return False
            self._requested = True
            return True

    def execute(self) -> None:
        """Stop the owned Codex child, then gracefully stop this app server."""

        with self._lock:
            if not self._requested or self._executed:
                return
            self._executed = True
            stop_hook = self._server_stop_hook

        try:
            # This closes only the singleton Codex App Server process created
            # and owned by FinancialFeeSystem.  User-started Codex processes
            # are never enumerated or touched.
            get_codex_app_server().close()
        finally:
            if stop_hook is not None:
                stop_hook()
                return
            _signal_running_server()


@lru_cache
def get_shutdown_coordinator() -> ShutdownCoordinator:
    return ShutdownCoordinator()

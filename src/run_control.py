from __future__ import annotations

import signal
import threading
import time
from contextlib import contextmanager
from enum import Enum
from typing import Iterator


class InterruptState(str, Enum):
    RUNNING = "running"
    GRACEFUL_STOP_REQUESTED = "graceful_stop_requested"
    FORCE_STOP_REQUESTED = "force_stop_requested"


class RunControl:
    def __init__(self, *, grace_period_seconds: float = 10.0) -> None:
        self._grace_period_seconds = grace_period_seconds
        self._lock = threading.RLock()
        self._state = InterruptState.RUNNING
        self._request_count = 0
        self._requested_at_monotonic: float | None = None

    def request_interrupt(self) -> InterruptState:
        with self._lock:
            return self._advance_state()

    def request_interrupt_from_signal(self) -> InterruptState:
        """Signal-safe interrupt, no lock -- GIL makes state transitions safe."""
        return self._advance_state()

    def _advance_state(self) -> InterruptState:
        self._request_count += 1
        if self._state == InterruptState.RUNNING:
            self._state = InterruptState.GRACEFUL_STOP_REQUESTED
            self._requested_at_monotonic = time.monotonic()
        elif self._state == InterruptState.GRACEFUL_STOP_REQUESTED:
            self._state = InterruptState.FORCE_STOP_REQUESTED
        return self._state

    @property
    def interrupt_state(self) -> InterruptState:
        with self._lock:
            return self._state

    @property
    def request_count(self) -> int:
        with self._lock:
            return self._request_count

    @property
    def stop_requested(self) -> bool:
        return self.interrupt_state != InterruptState.RUNNING

    @property
    def graceful_stop_requested(self) -> bool:
        return self.interrupt_state == InterruptState.GRACEFUL_STOP_REQUESTED

    @property
    def force_stop_requested(self) -> bool:
        return self.interrupt_state == InterruptState.FORCE_STOP_REQUESTED

    @property
    def grace_period_seconds(self) -> float:
        return self._grace_period_seconds

    def grace_period_exceeded(self, now: float | None = None) -> bool:
        with self._lock:
            if self._state != InterruptState.GRACEFUL_STOP_REQUESTED:
                return False
            if self._requested_at_monotonic is None:
                return False
            current = time.monotonic() if now is None else now
            return (current - self._requested_at_monotonic) >= self._grace_period_seconds

    def state_label(self) -> str:
        state = self.interrupt_state
        if state == InterruptState.RUNNING:
            return "running"
        if state == InterruptState.GRACEFUL_STOP_REQUESTED:
            return "stopping"
        return "force-stop"


@contextmanager
def activate_interrupt_handler(control: RunControl) -> Iterator[None]:
    previous_sigint = signal.getsignal(signal.SIGINT)
    previous_sigterm = signal.getsignal(signal.SIGTERM)

    def _handle_signal(_signum: int, _frame: object | None) -> None:
        control.request_interrupt_from_signal()
        if control._request_count >= 3:
            raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)

"""
Plain-language version: imagine calling a friend who isn't picking up.
After a few unanswered calls in a row, you stop dialing for a while
instead of calling again every single minute and waiting for it to
ring out each time. After a cooldown, you try ONE more call to see if
they're back — if they answer, you go back to calling normally; if
not, you wait again.

That's a circuit breaker. Without one, if Redis is fully down, every
single request still pays the full connection-timeout cost (2 seconds,
per our socket_timeout setting) trying to reach it before giving up.
With one, after a few failures in a row we stop trying entirely for a
while and fail immediately — much faster degradation, and it stops
hammering a service that's already struggling to recover.

Three states:
  CLOSED     - normal operation, calls go through as usual.
  OPEN       - too many recent failures; calls fail immediately
               without even being attempted, until the cooldown passes.
  HALF_OPEN  - cooldown has passed; allow exactly one call through as
               a test. Success -> back to CLOSED. Failure -> back to
               OPEN, cooldown restarts.
"""

import time
from enum import Enum
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")


class CircuitOpenError(Exception):
    """Raised instead of even attempting the call, while the circuit
    is OPEN. Callers should catch this exactly like any other failure
    of the underlying dependency — the point is it fails FASTER, not
    that it behaves differently."""


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, recovery_timeout_seconds: float = 15.0):
        self._failure_threshold = failure_threshold
        self._recovery_timeout_seconds = recovery_timeout_seconds
        self._failure_count = 0
        self._state = CircuitState.CLOSED
        self._opened_at: float | None = None

    @property
    def state(self) -> CircuitState:
        # Lazily transitions OPEN -> HALF_OPEN once the cooldown has
        # elapsed, the moment anyone checks — no background timer
        # needed.
        if self._state == CircuitState.OPEN and self._opened_at is not None:
            if time.monotonic() - self._opened_at >= self._recovery_timeout_seconds:
                self._state = CircuitState.HALF_OPEN
        return self._state

    async def call(self, func: Callable[[], Awaitable[T]]) -> T:
        if self.state == CircuitState.OPEN:
            raise CircuitOpenError("Circuit is open; failing fast without attempting the call")

        try:
            result = await func()
        except Exception:
            self._record_failure()
            raise
        else:
            self._record_success()
            return result

    def _record_failure(self) -> None:
        self._failure_count += 1
        if self._failure_count >= self._failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()

    def _record_success(self) -> None:
        self._failure_count = 0
        self._state = CircuitState.CLOSED
        self._opened_at = None

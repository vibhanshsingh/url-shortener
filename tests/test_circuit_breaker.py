"""
Same state machine verified manually during development — captured
here as a permanent, repeatable test rather than a one-off script.
"""

import asyncio

import pytest

from app.core.circuit_breaker import CircuitBreaker, CircuitOpenError, CircuitState


async def failing_call():
    raise ConnectionError("dependency is down")


async def working_call():
    return "ok"


class TestCircuitBreaker:
    async def test_starts_closed(self):
        breaker = CircuitBreaker(failure_threshold=3, recovery_timeout_seconds=15.0)
        assert breaker.state == CircuitState.CLOSED

    async def test_stays_closed_below_failure_threshold(self):
        breaker = CircuitBreaker(failure_threshold=3, recovery_timeout_seconds=15.0)
        for _ in range(2):
            with pytest.raises(ConnectionError):
                await breaker.call(failing_call)
        assert breaker.state == CircuitState.CLOSED

    async def test_opens_at_failure_threshold(self):
        breaker = CircuitBreaker(failure_threshold=3, recovery_timeout_seconds=15.0)
        for _ in range(3):
            with pytest.raises(ConnectionError):
                await breaker.call(failing_call)
        assert breaker.state == CircuitState.OPEN

    async def test_open_circuit_fails_fast_without_attempting_call(self):
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout_seconds=15.0)
        with pytest.raises(ConnectionError):
            await breaker.call(failing_call)
        assert breaker.state == CircuitState.OPEN

        # This call would succeed if attempted — but the circuit is
        # open, so it should fail fast with CircuitOpenError instead
        # of ever actually running working_call.
        with pytest.raises(CircuitOpenError):
            await breaker.call(working_call)

    async def test_transitions_to_half_open_after_cooldown(self):
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout_seconds=0.05)
        with pytest.raises(ConnectionError):
            await breaker.call(failing_call)
        assert breaker.state == CircuitState.OPEN

        await asyncio.sleep(0.1)
        assert breaker.state == CircuitState.HALF_OPEN

    async def test_successful_half_open_call_closes_circuit(self):
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout_seconds=0.05)
        with pytest.raises(ConnectionError):
            await breaker.call(failing_call)
        await asyncio.sleep(0.1)

        result = await breaker.call(working_call)

        assert result == "ok"
        assert breaker.state == CircuitState.CLOSED

    async def test_failed_half_open_call_reopens_circuit(self):
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout_seconds=0.05)
        with pytest.raises(ConnectionError):
            await breaker.call(failing_call)
        await asyncio.sleep(0.1)

        with pytest.raises(ConnectionError):
            await breaker.call(failing_call)

        assert breaker.state == CircuitState.OPEN

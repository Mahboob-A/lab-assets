"""
circuit_breaker.py - Production-Grade State Machine Circuit Breaker.

Protects upstream callers from cascading failures and latency degradation
when the primary Elasticsearch cluster is saturated, timing out, or unavailable.
Implements the canonical 3-state finite state machine (FSM):
  - CLOSED: Normal operation. Primary calls flow unimpeded.
  - OPEN: Saturated/Failing. Primary is quarantined; calls divert to fallback instantly.
  - HALF-OPEN: Cooldown elapsed. A single canary probe tests if primary has recovered.
"""

import time
import threading
from enum import Enum
from typing import Dict, Any, Optional


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class SearchCircuitBreaker:
    """
    Thread-safe Finite State Machine Circuit Breaker for Search Query Routing.
    
    Attributes:
        failure_threshold (int): Consecutive failures/timeouts to trip CLOSED -> OPEN.
        recovery_timeout (float): Seconds to stay OPEN before attempting HALF_OPEN canary.
        timeout_sla (float): Request SLA latency cap (seconds) before marking as failure.
        half_open_probes (int): Consecutive canary successes required to reset to CLOSED.
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout: float = 10.0,
        timeout_sla: float = 0.500,
        half_open_probes: int = 1,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.timeout_sla = timeout_sla
        self.half_open_probes = half_open_probes

        self._lock = threading.Lock()
        self.state: CircuitState = CircuitState.CLOSED
        self.failure_count: int = 0
        self.success_count: int = 0
        self.canary_in_flight: bool = False
        self.last_state_change: float = time.time()
        self.last_failure_time: Optional[float] = None
        self.last_error: Optional[str] = None

    def can_execute(self) -> bool:
        """
        Determines whether a request may be routed to the primary engine.

        # TODO 1: Implement State Transition Logic for can_execute()
        # 1. If state == CLOSED, return True immediately.
        # 2. If state == OPEN:
        #    Check if (current_time - last_failure_time) >= recovery_timeout.
        #    - If cooldown has elapsed, transition state to HALF_OPEN, reset success_count,
        #      mark canary_in_flight = True, and return True (allow single canary probe).
        #    - Otherwise, return False (quarantine continues, fail fast).
        # 3. If state == HALF_OPEN:
        #    Allow only a single in-flight canary probe; divert concurrent calls to fallback.
        """
        current_time = time.time()

        with self._lock:
            if self.state == CircuitState.CLOSED:
                return True

            if self.state == CircuitState.OPEN:
                if self.last_failure_time and (current_time - self.last_failure_time) >= self.recovery_timeout:
                    self.state = CircuitState.HALF_OPEN
                    self.last_state_change = current_time
                    self.success_count = 0
                    self.canary_in_flight = True
                    return True
                return False

            if self.state == CircuitState.HALF_OPEN:
                if not self.canary_in_flight:
                    self.canary_in_flight = True
                    return True
                return False

            return False

    def record_success(self) -> None:
        """
        Records a successful primary request within SLA.

        # TODO 2: Implement Recovery Logic for record_success()
        # 1. Clear canary_in_flight flag.
        # 2. If state == HALF_OPEN:
        #    Increment success_count.
        #    If success_count >= half_open_probes:
        #      Transition state to CLOSED.
        #      Reset failure_count to 0 and last_error to None.
        # 3. If state == CLOSED:
        #    Reset failure_count to 0.
        """
        with self._lock:
            self.canary_in_flight = False
            if self.state == CircuitState.HALF_OPEN:
                self.success_count += 1
                if self.success_count >= self.half_open_probes:
                    self.state = CircuitState.CLOSED
                    self.last_state_change = time.time()
                    self.failure_count = 0
                    self.last_error = None
            elif self.state == CircuitState.CLOSED:
                self.failure_count = 0

    def record_failure(self, error: str = "Request failed or exceeded SLA") -> None:
        """
        Records a primary engine failure or latency SLA breach.

        If state is CLOSED and failure_count >= failure_threshold:
          Trip state to OPEN and record last_failure_time.
        If state is HALF_OPEN:
          A canary probe failed! Immediately trip back to OPEN and refresh cooldown.
        """
        current_time = time.time()
        with self._lock:
            self.canary_in_flight = False
            self.last_failure_time = current_time
            self.last_error = error

            if self.state == CircuitState.HALF_OPEN:
                # Canary probe failed: immediately abort recovery and revert to OPEN
                self.state = CircuitState.OPEN
                self.last_state_change = current_time
                self.failure_count = self.failure_threshold
            elif self.state == CircuitState.CLOSED:
                self.failure_count += 1
                if self.failure_count >= self.failure_threshold:
                    self.state = CircuitState.OPEN
                    self.last_state_change = current_time

    def trip(self, reason: str = "Manual administrative trip") -> None:
        """Administrative / chaos endpoint to force the circuit OPEN immediately."""
        with self._lock:
            self.state = CircuitState.OPEN
            self.failure_count = self.failure_threshold
            self.canary_in_flight = False
            self.last_failure_time = time.time()
            self.last_state_change = time.time()
            self.last_error = reason

    def reset(self) -> None:
        """Administrative endpoint to force reset the circuit to CLOSED."""
        with self._lock:
            self.state = CircuitState.CLOSED
            self.failure_count = 0
            self.success_count = 0
            self.canary_in_flight = False
            self.last_state_change = time.time()
            self.last_failure_time = None
            self.last_error = None

    def get_status(self) -> Dict[str, Any]:
        """Returns the real-time observability telemetry of the circuit breaker."""
        with self._lock:
            now = time.time()
            cooldown_remaining = 0.0
            if self.state == CircuitState.OPEN and self.last_failure_time:
                elapsed = now - self.last_failure_time
                cooldown_remaining = max(0.0, round(self.recovery_timeout - elapsed, 2))

            return {
                "state": self.state.value,
                "failure_count": self.failure_count,
                "failure_threshold": self.failure_threshold,
                "timeout_sla_ms": self.timeout_sla * 1000.0,
                "recovery_timeout_s": self.recovery_timeout,
                "cooldown_remaining_s": cooldown_remaining,
                "canary_in_flight": self.canary_in_flight,
                "last_error": self.last_error,
                "uptime_in_state_s": round(now - self.last_state_change, 2),
            }


if __name__ == "__main__":
    print("=== Testing SearchCircuitBreaker State Transitions ===")
    cb = SearchCircuitBreaker(failure_threshold=3, recovery_timeout=2.0, timeout_sla=0.5)

    print(f"Initial state: {cb.state.value} (can_execute: {cb.can_execute()})")
    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute() is True

    # 1. Simulate 2 transient failures (below threshold)
    cb.record_failure("Transient network blip 1")
    cb.record_failure("Transient network blip 2")
    print(f"After 2 failures: state={cb.state.value}, count={cb.failure_count}")
    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute() is True

    # 2. Simulate 3rd failure (trips threshold)
    cb.record_failure("Timeout 503 from primary")
    print(f"After 3rd failure: state={cb.state.value}, count={cb.failure_count}")
    assert cb.state == CircuitState.OPEN
    assert cb.can_execute() is False

    # 3. Fast fail while OPEN
    print(f"Immediate call while OPEN: can_execute={cb.can_execute()} (Fast-fail active!)")
    assert cb.can_execute() is False

    # 4. Wait for recovery timeout
    print(f"Waiting {cb.recovery_timeout}s for cooldown to elapse...")
    time.sleep(cb.recovery_timeout + 0.1)

    # 5. Check transition to HALF_OPEN
    print(f"After cooldown: can_execute={cb.can_execute()}, state={cb.state.value}")
    assert cb.state == CircuitState.HALF_OPEN
    assert cb.canary_in_flight is True
    # Verify second concurrent call is blocked during canary
    assert cb.can_execute() is False, "Concurrent call during canary probe should be blocked"

    # 6. Canary succeeds -> resets to CLOSED
    cb.record_success()
    print(f"After successful canary: state={cb.state.value}, failure_count={cb.failure_count}")
    assert cb.state == CircuitState.CLOSED
    assert cb.failure_count == 0
    assert cb.canary_in_flight is False

    print("\n[PASSED] All SearchCircuitBreaker state transitions verified successfully!")

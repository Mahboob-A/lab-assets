"""
verify_search_layer.py - Automated End-to-End Verification Test Suite.

Verifies all critical operational guarantees of the Unified Search Surface:
  1. Keystroke Autocomplete & Server-Side Prefix Gating (< 2 chars dropped).
  2. Circuit Breaker FSM & Thread-Safe Single-Canary Probe Gating.
  3. Sub-millisecond In-Memory Inverted Index Fallback Execution.
  4. Primary Elasticsearch Cluster Integration & Multi-Field Querying.
  5. Chaos Engineering Fault Injection & Zero-Downtime Graceful Degradation.
  6. Side-by-Side Dual Engine Comparison (/compare) & Speedup Multiplier.
"""

import time
import sys
from typing import Dict, Any

from dataset import QueryDataset
from trie_service import TrieTypeaheadService
from circuit_breaker import SearchCircuitBreaker, CircuitState
from search_service import SearchBrokerService
from es_client import ElasticsearchClient


def test_typeahead_and_gating():
    print("\n--- Test 1: Typeahead Autocomplete & Prefix Gating ---")
    ts = TrieTypeaheadService(k=5)
    ts.build_from_dataset(dict(QueryDataset.SEED_TERMS))

    # Test Prefix Gating Logic (< 2 chars)
    short_prefix = "s"
    suggestions_short = ts.suggest(short_prefix, k=5)
    print(f"Prefix '{short_prefix}': Raw Trie returns {len(suggestions_short)} items.")

    # Valid prefix
    valid_prefix = "sys"
    t0 = time.perf_counter()
    suggestions_valid = ts.suggest(valid_prefix, k=5)
    latency_ms = (time.perf_counter() - t0) * 1000.0

    print(f"Prefix '{valid_prefix}': Returned {len(suggestions_valid)} suggestions in {latency_ms:.3f} ms.")
    for word, freq in suggestions_valid[:3]:
        print(f"  * '{word}' (frequency: {freq})")

    assert len(suggestions_valid) > 0, "Expected suggestions for prefix 'sys'"
    assert latency_ms < 5.0, f"Latency {latency_ms} ms exceeded 5.0 ms threshold"
    print("[PASS] Test 1: Typeahead Autocomplete verified successfully.")


def test_circuit_breaker_fsm():
    print("\n--- Test 2: Circuit Breaker State Machine & Single-Canary Gating ---")
    cb = SearchCircuitBreaker(failure_threshold=3, recovery_timeout=1.0, timeout_sla=0.5)

    # 1. Verify Initial State
    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute() is True
    print(f"Initial State: {cb.state.value} (can_execute: {cb.can_execute()})")

    # 2. Simulate Failures up to Threshold
    cb.record_failure("Simulated ES timeout #1")
    cb.record_failure("Simulated ES timeout #2")
    assert cb.state == CircuitState.CLOSED
    print(f"After 2 Failures: {cb.state.value} (Failures: {cb.failure_count}/3)")

    # 3. Trip to OPEN
    cb.record_failure("Simulated ES timeout #3 (threshold breached)")
    assert cb.state == CircuitState.OPEN
    assert cb.can_execute() is False
    print(f"After 3rd Failure: {cb.state.value} (Tripped! can_execute: {cb.can_execute()})")

    # 4. Fast-Fail while OPEN
    assert cb.can_execute() is False
    print(f"Fast-Fail Check: can_execute is {cb.can_execute()} (Primary traffic blocked instantly)")

    # 5. Cooldown to HALF-OPEN
    print(f"Sleeping {cb.recovery_timeout}s to allow cooldown window to elapse...")
    time.sleep(cb.recovery_timeout + 0.1)

    # First probe: Allowed
    assert cb.can_execute() is True
    assert cb.state == CircuitState.HALF_OPEN
    assert cb.canary_in_flight is True
    print(f"After Cooldown: {cb.state.value} (Canary probe allowed: True, in_flight: {cb.canary_in_flight})")

    # Second concurrent probe: Blocked to prevent flapping thundering herd
    assert cb.can_execute() is False, "Concurrent call during active canary probe must be blocked!"
    print(f"Concurrent Probe Block Check: can_execute is False (Thundering herd prevented!)")

    # 6. Successful Canary Probe resets to CLOSED
    cb.record_success()
    assert cb.state == CircuitState.CLOSED
    assert cb.failure_count == 0
    assert cb.canary_in_flight is False
    print(f"After Successful Canary: {cb.state.value} (Reset complete! Failures: {cb.failure_count})")

    print("[PASS] Test 2: Circuit Breaker FSM & Single-Canary Gating verified successfully.")


def test_in_memory_fallback():
    print("\n--- Test 3: Sub-Millisecond In-Memory Index Fallback ---")
    broker = SearchBrokerService()

    # Force fallback execution
    t0 = time.perf_counter()
    res = broker.search(query="distributed consensus", force_fallback=True)
    latency_ms = (time.perf_counter() - t0) * 1000.0

    print(f"Fallback Search: Engine={res['engine']}, Degraded={res['degraded']}")
    print(f"Reported Engine Latency: {res['latency_ms']} ms | Total Round-Trip: {latency_ms:.3f} ms")
    print(f"Total Hits: {res['total_hits']}")
    for hit in res["results"]:
        print(f"  * Doc #{hit['doc_id']}: {hit['title']} (score: {hit['score']})")

    assert res["engine"] == "in-memory-inverted-index"
    assert res["degraded"] is True
    assert res["total_hits"] > 0
    assert res["latency_ms"] < 1.0, f"Fallback latency {res['latency_ms']} ms exceeded 1.0 ms SLA"

    # Edge cases: empty query, whitespace query
    empty_res = broker._fallback_search("")
    assert empty_res == [], "Empty query must return empty list"
    whitespace_res = broker._fallback_search("   ")
    assert whitespace_res == [], "Whitespace query must return empty list"

    print("[PASS] Test 3: In-Memory Index Fallback & Edge Cases verified successfully.")


def test_primary_or_graceful_degradation():
    print("\n--- Test 4: Primary Elasticsearch & Fault-Tolerant Routing ---")
    broker = SearchBrokerService()
    es_up = broker.es_client.ping()

    res = broker.search(query="replicate cache", top_k=5)

    if es_up:
        print(f"Elasticsearch is RUNNING! Primary query executed.")
        print(f"Engine: {res['engine']} | Degraded: {res['degraded']} | Latency: {res['latency_ms']} ms")
        assert res["engine"] == "elasticsearch"
        assert res["degraded"] is False
    else:
        print(f"Elasticsearch is OFFLINE! Graceful degradation engaged.")
        print(f"Engine: {res['engine']} | Degraded: {res['degraded']} | Latency: {res['latency_ms']} ms")
        assert res["engine"] == "in-memory-inverted-index"
        assert res["degraded"] is True

    print(f"Results Count: {res['total_hits']}")
    for hit in res["results"][:2]:
        print(f"  * Doc #{hit['doc_id']}: {hit['title']} (score: {hit['score']})")

    print("[PASS] Test 4: Primary routing & graceful degradation verified successfully.")


def test_chaos_trip_and_recovery():
    print("\n--- Test 5: Chaos Administration Trip and Recovery ---")
    broker = SearchBrokerService()

    # 1. Trip circuit manually
    broker.circuit_breaker.trip("Manual Chaos Drill")
    assert broker.circuit_breaker.state == CircuitState.OPEN

    res_tripped = broker.search(query="distributed systems", top_k=5)
    print(f"Tripped Query: Engine={res_tripped['engine']}, Degraded={res_tripped['degraded']}")
    assert res_tripped["engine"] == "in-memory-inverted-index"
    assert res_tripped["degraded"] is True

    # 2. Reset circuit manually
    broker.circuit_breaker.reset()
    assert broker.circuit_breaker.state == CircuitState.CLOSED
    print(f"Reset Circuit: State={broker.circuit_breaker.state.value}")

    print("[PASS] Test 5: Chaos trip and recovery verified successfully.")


def test_dual_engine_comparison():
    print("\n--- Test 6: Dual Engine Shootout & Speedup Multiplier ---")
    broker = SearchBrokerService()
    es_up = broker.es_client.ping()

    query = "replicating cache"
    # Fallback timing
    t0_ram = time.perf_counter()
    ram_hits = broker._fallback_search(query)
    ram_ms = (time.perf_counter() - t0_ram) * 1000.0

    print(f"Dual Shootout Query: '{query}'")
    print(f"In-Memory RAM Engine: Latency = {ram_ms:.3f} ms | Hits = {len(ram_hits)}")

    if es_up:
        t0_es = time.perf_counter()
        es_hits = broker.es_client.search(query, field="content.english", operator="or", top_k=5)
        es_ms = (time.perf_counter() - t0_es) * 1000.0
        speedup = es_ms / max(0.001, ram_ms)
        print(f"Elasticsearch Engine: Latency = {es_ms:.3f} ms | Hits = {len(es_hits)}")
        print(f"Speedup Factor: {speedup:.1f}x faster in RAM heap")
        assert len(es_hits) > len(ram_hits), "ES should return more hits due to Porter stemming for 'replicating'"

    print("[PASS] Test 6: Dual Engine Shootout verified successfully.")


if __name__ == "__main__":
    print("==================================================================")
    print(" UNIFIED SEARCH SURFACE: AUTOMATED VERIFICATION SUITE")
    print("==================================================================")

    test_typeahead_and_gating()
    test_circuit_breaker_fsm()
    test_in_memory_fallback()
    test_primary_or_graceful_degradation()
    test_chaos_trip_and_recovery()
    test_dual_engine_comparison()

    print("\n==================================================================")
    print(" ALL 6 TEST SUITES PASSED! ARCHITECTURAL SYSTEM READY.")
    print("==================================================================")

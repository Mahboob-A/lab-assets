"""
Main Verification Script for Typeahead and Autocomplete Services.

Loads seed query dataset, populates both the Prefix Trie and the Optimized Hash+Heap
services, validates suggestion parity across standard test prefixes, and compares
memory footprints and query latencies.
"""

import time
from dataset import QueryDataset
from trie_service import TrieTypeaheadService
from hash_heap_service import HashHeapTypeaheadService


def run_verification():
    print("=" * 76)
    print("  TYPEAHEAD & AUTOCOMPLETE LAB: TRIE vs HASH+HEAP PARITY VERIFICATION")
    print("=" * 76)

    # 1. Load Dataset
    dataset = QueryDataset()
    queries = dataset.queries
    print(f"[*] Loaded curated dataset: {len(queries)} technical search queries")

    # 2. Build Trie Engine
    t0 = time.perf_counter()
    trie = TrieTypeaheadService(k=5)
    trie.build_from_dataset(queries, k=5)
    trie_build_ms = (time.perf_counter() - t0) * 1000
    trie_nodes, trie_bytes = trie.count_nodes_and_memory()
    print(f"[+] Built Trie Service in {trie_build_ms:.2f}ms")
    print(f"    - Total Nodes: {trie_nodes:,} | Estimated RAM: {trie_bytes / 1024:.2f} KB")

    # 3. Build Hash+Heap Engine
    t0 = time.perf_counter()
    hash_heap = HashHeapTypeaheadService(max_prefix_len=6, k=5)
    hash_heap.build(queries, max_prefix_len=6)
    hash_build_ms = (time.perf_counter() - t0) * 1000
    hash_bytes = hash_heap.memory_footprint_bytes()
    print(f"[+] Built Hash+Heap Service in {hash_build_ms:.2f}ms")
    print(f"    - Unique Prefixes: {len(hash_heap.prefix_index):,} | Estimated RAM: {hash_bytes / 1024:.2f} KB")

    # 4. Parity Testing Across Standard Prefixes
    test_prefixes = ["sys", "py", "rea", "do", "red", "sql"]
    print("\n" + "=" * 76)
    print("  SUGGESTION PARITY CHECK (Top-5 Suggestions for Test Prefixes)")
    print("=" * 76)

    all_match = True
    for prefix in test_prefixes:
        trie_results = trie.suggest(prefix, k=5)
        hash_results = hash_heap.suggest(prefix, k=5)

        # Check if query names and ranks match
        trie_names = [q for q, _ in trie_results]
        hash_names = [q for q, _ in hash_results]
        match = trie_names == hash_names
        status = "MATCH [PASS]" if match else "MISMATCH [FAIL]"
        if not match:
            all_match = False

        print(f"\nPrefix: '{prefix}' -> Status: {status}")
        print(f"  Trie Results      : {trie_results[:3]}")
        print(f"  Hash+Heap Results : {hash_results[:3]}")

    assert all_match, "Parity check failed between Trie and Hash+Heap services"
    print("\n" + "-" * 76)
    print("[SUCCESS] All test prefixes yielded 100% identical top-K ranked suggestions!")
    print("-" * 76)

    # 5. Single-Query Latency Micro-Benchmark
    print("\n" + "=" * 76)
    print("  SINGLE-QUERY LATENCY MICRO-BENCHMARK (1,000 iterations for 'sys')")
    print("=" * 76)

    iterations = 1000
    t0 = time.perf_counter()
    for _ in range(iterations):
        _ = trie.suggest("sys", k=5)
    trie_single_us = ((time.perf_counter() - t0) / iterations) * 1_000_000

    t0 = time.perf_counter()
    for _ in range(iterations):
        _ = hash_heap.suggest("sys", k=5)
    hash_single_us = ((time.perf_counter() - t0) / iterations) * 1_000_000

    print(f"  Trie O(L) Lookup Latency      : {trie_single_us:.3f} microseconds ({trie_single_us / 1000:.5f} ms)")
    print(f"  Hash+Heap Lookup Latency      : {hash_single_us:.3f} microseconds ({hash_single_us / 1000:.5f} ms)")
    print("=" * 76)


if __name__ == "__main__":
    run_verification()

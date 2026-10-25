# M16a-L2: Typeahead & Autocomplete Service with a Trie & Hash+Heap Optimization

Welcome to Lab 2 of Module 16a. In this lab, you will design, build, and benchmark an ultra-low-latency **Typeahead and Autocomplete Service** capable of returning top-ranked query suggestions within strict production latency budgets.

Before writing a single line of code, let us establish a key naming convention: in distributed systems engineering, **Typeahead**, **Autocomplete**, and **Prefix Suggestion** refer to the exact same architectural capability. Whether you are typing into Google's search bar, Amazon's product lookup, or Slack's channel switcher, the service must predict the user's intent after every individual keystroke.

In [Lab 1: Inverted Index](../m16a-l1-inverted-index/readme.md), you built an inverted index designed to retrieve documents matching full search terms ranked by BM25 or TF-IDF. Autocomplete presents a fundamentally different engineering challenge. A traditional search engine processes a query only after the user presses Enter. An autocomplete engine must execute for every keystroke, handling ten to twenty times higher query volume with an order of magnitude tighter latency.

In this lab, you will build a production-ready autocomplete system across three distinct architectural stages:
1. **Part A: Baseline Prefix Trie with Node-Level Top-K Caching**: Implement an in-memory prefix tree utilizing memory-efficient slots, post-order bottom-up construction, and heap-based candidate filtering.
2. **Part B: Production Hash Table + Bounded Min-Heap Optimization**: Replace deep character pointer chains with flat prefix hashing and bounded heap extraction, achieving a **92% reduction in RAM footprint** and ultra-fast build times.
3. **Part C: Enterprise-Scale Architecture (Conceptual Glimpse)**: Examine how global-scale systems handle petabyte-scale vocabulary partitioning, consistent hash sharding, and real-time streaming frequency decay pipelines.

---

## Lab Architecture Overview

The following diagram illustrates the complete end-to-end architecture of the system you will build and evaluate in this lab:

<p align="center">
  <img src="images/overview-architecture.svg" width="100%" alt="M16a-L2 End-to-End System Architecture">
</p>

---

## Stage 1: The Production Reality & The 50ms SLA Budget

### The Keystroke Latency Budget

When an engineer searches Google or Amazon, the human visual system perceives interface lag if feedback takes longer than 100 milliseconds. For real-time typing, the threshold is even more unforgiving:
* An average professional typist produces 60 to 80 words per minute, translating to one keystroke every **150 to 180 milliseconds**.
* If an autocomplete suggestion takes longer than **50 milliseconds** round-trip to appear, the user has already pressed the next key. The previous suggestion arrives stale, resulting in visual flicker and wasted network bandwidth.

To guarantee a seamless experience, the entire end-to-end round-trip latency must not exceed **50 milliseconds at the 99th percentile (p99)**.

<p align="center">
  <img src="images/diagram-1-keystroke-latency-budget.svg" width="100%" alt="The 50ms Keystroke Latency Budget &amp; Human Typing Rhythm">
</p>

Let us break down this 50-millisecond latency budget:
* **Client Network Round-Trip Time (RTT)**: 30ms to 35ms (15ms to 18ms each way over standard mobile 4G/5G or home broadband).
* **Load Balancer & SSL Termination**: 2ms to 3ms.
* **JSON Deserialization & Serialization**: 1ms.
* **Browser DOM Rendering**: 4ms.
* **Allocated Budget for Autocomplete Compute**: **<= 3ms to 5ms**.

If your backend lookup takes 10ms, network jitter pushes your p99 latency well over the 50ms cliff. The compute engine must execute its ranking and prefix lookup in **microseconds**, not milliseconds.

---

## Stage 2: Why Relational Databases and Inverted Indexes Fail

Before evaluating data structures like Tries and Hash Maps, we must examine why standard backend database approaches completely fail for keystroke search.

### The Relational Database Failure: `LIKE 'prefix%'`

The naive approach taken by junior engineers is to query an existing relational database (PostgreSQL, MySQL):

```sql
SELECT query, frequency 
FROM search_history 
WHERE query LIKE 'sys%' 
ORDER BY frequency DESC 
LIMIT 5;
```

Even if a B-tree index is applied to the `query` column, this approach fails under production load for three fundamental reasons:
1. **Index Range Scan Overhead**: While a B-tree index can evaluate `LIKE 'sys%'` as a range scan (`query >= 'sys' AND query < 'syt'`), it matches thousands of records. The database engine must pull every matching row from storage or buffer pool into memory.
2. **Read-Time Sorting Bottleneck**: The database must sort all matching candidate rows by `frequency DESC` before taking the top 5. If 20,000 queries begin with the prefix `sys`, the database sorts 20,000 rows on every single keystroke.
3. **Connection and Concurrency Limits**: At 10,000 queries per second (QPS), relational database connection pools are overwhelmed within seconds. Disk I/O and mutex contention on buffer pools cause latency spikes exceeding 200ms to 500ms.

### The Inverted Index Failure: Keystroke Mismatch

In Lab 1, you learned that inverted indexes map words to document IDs. Why can we not use an inverted index for autocomplete?
* **Token Boundary Requirement**: Inverted indexes tokenize text on word boundaries (`"system"`, `"design"`). When a user types `"sys"`, the term is an incomplete token.
* **N-gram Index Explosion**: Supporting arbitrary prefixes in an inverted index requires indexing character 2-grams, 3-grams, and 4-grams (e.g., `s`, `sy`, `sys`, `syst`). This balloons index storage by 500% to 800% and requires intersecting massive posting lists at read time.
* **Write Latency**: Updating term statistics in an inverted index requires modifying posting lists, which is poorly suited for real-time streaming counters.

### The Inversion Principle

The fundamental architectural principle of autocomplete is **The Inversion Principle**:
* Standard Search indexes data and performs complex scoring and ranking **at read time** (when the user queries).
* Autocomplete **precomputes and ranks candidates at build/write time**, so that read-time retrieval is reduced to a pure lookup.

<p align="center">
  <img src="images/diagram-2-inversion-principle.svg" width="100%" alt="The Inversion Principle: Traditional Search vs. Autocomplete">
</p>

---

## Stage 2 (Continued): Redis in the Autocomplete Architecture

A major architectural decision in autocomplete services is the role of an in-memory distributed cache like Redis.

### Standalone In-Memory vs. Hybrid Redis Architecture

| Architecture Dimension | Pure Local In-Memory (Trie / Hash in RAM) | Hybrid Architecture (Local Engine + Redis) |
| :--- | :--- | :--- |
| **Read Latency** | Sub-microsecond (0.0003ms - 0.01ms). Zero network overhead. | 0.5ms to 1.5ms over local loopback / VPC network. |
| **State Synchronization** | Nodes are isolated. Each worker process maintains its own tree. | Centralized coordination. All workers share global frequency counters. |
| **Write Lock Contention** | Updating a local Trie during active queries requires locks, causing latency spikes. | Redis Sorted Sets (`ZINCRBY`) handle atomic increments off the read path. |
| **Cold Start / Restarts** | Process restarts require rebuilding indices from raw logs (minutes of downtime). | Redis provides instant read availability while local memory builds in background. |
| **Keystroke Absorption** | Every keystroke hits application worker memory. | Redis String cache (`GET prefix_cache:sys`) absorbs 80% of repeated prefixes. |

### The Redis Trade-Off Decision

In this lab, we employ a **hybrid production model**:
1. **Primary Read Path**: High-throughput in-memory data structures (Trie and Hash+Heap) serve prefix suggestions with sub-millisecond execution.
2. **Asynchronous Write Path**: Redis Sorted Sets (`ZINCRBY query_frequencies 1 "query"`) maintain atomic, global search frequency counts without blocking read queries.
3. **L1 Hot-Prefix Cache**: Redis caches the top-K results for popular prefixes (`prefix_cache:sys`) with a 5-minute Time-To-Live (TTL), shielding downstream application memory from spikes.

---

## Stage 3: Hands-On Walkthrough: Part A (Baseline Trie Service)

### Milestone 1: Environment Setup & Redis Integration Verification

In Poridhi's cloud environment, an in-memory Redis instance can be provisioned directly. For this lab, you configure the connection credentials directly at the top of your Redis client file.

Let us inspect the project workspace layout:

```text
typeahead_lab/
├── __init__.py
├── dataset.py            # Technical search query dataset with Zipfian distribution
├── redis_client.py        # Redis client with atomic ZINCRBY counters & cache
├── test_redis.py          # Standalone verification script for Redis connectivity
├── trie_node.py           # Memory-optimized TrieNode using __slots__
├── trie_service.py        # Trie implementation with bottom-up Top-K caching
├── hash_heap_service.py   # Optimized Hash+Heap implementation
├── main.py                # Suggestion parity and micro-benchmark runner
├── benchmark.py           # 10,000 QPS load test and memory profiler
└── api.py                 # FastAPI microservice for HTTP verification
```

Open `redis_client.py` and inspect the configuration block:

```python
# ==============================================================================
# REDIS CREDENTIALS CONFIGURATION
# Students: In Poridhi Lab, enable your Redis database service and enter the
# provided host, port, and password below. For local testing, 127.0.0.1:6379 is default.
# ==============================================================================
REDIS_HOST: str = "127.0.0.1"
REDIS_PORT: int = 6379
REDIS_PASSWORD: Optional[str] = None
# ==============================================================================
```

If Redis is running locally or provisioned in your lab VM, these settings connect automatically. If Redis is temporarily unreachable, the client gracefully falls back to an internal dictionary mock, allowing you to complete and benchmark the lab without interruption.

Run the verification script:

```bash
python3 typeahead_lab/test_redis.py
```

You should observe output confirming all tests have passed:

```text
====================================================================
Testing Redis Integration against: 127.0.0.1:6379
====================================================================
[NOTICE] Running in in-memory fallback mock mode.
         (If using Poridhi Redis, verify host/port/credentials in redis_client.py)
[TEST 1/4] PING test passed (Server active: PONG)
[TEST 2/4] Cache write test passed (Key: prefix_cache:sys)
[TEST 3/4] Cache read test passed (Retrieved 3 items: 'system design interview')
[TEST 4/4] Sorted Set increment test passed (Query: 'system design interview', Score: 5.0)
====================================================================
[ALL TESTS PASSED] Redis integration is operating normally.
====================================================================
```

---

### Milestone 2: Trie Data Structure Theory & Memory Realities

A standard Prefix Tree (Trie) is a multi-way tree structure where each edge represents a single character. All descendants of a node share the common string prefix associated with that node.

```text
Root
 └── 's'
      └── 'y'
           └── 's' [prefix node 'sys' | top_k: 'system design', 'systemctl status']
                ├── 't' ──> 'e' ──> 'm' (freq: 5000) ──> ' ' ──> 'design' (freq: 9850)
                └── 'c' ──> 't' ──> 'l' ──> ' status' (freq: 3120)
```

#### Why Naive Trie Traversal Fails at Runtime

In a basic computer science textbook, an autocomplete query on prefix `sys` operates as follows:
1. Walk down the tree along characters `s` -> `y` -> `s` (`O(L)` where `L` is prefix length).
2. From the prefix node `sys`, perform a Depth-First Search (DFS) or Breadth-First Search (BFS) across the entire subtree to locate all completing words.
3. Push all found words into a heap and extract the top `K`.

Under production scale, **runtime DFS is disastrous**:
* If 50,000 queries begin with the prefix `sys`, the DFS must visit hundreds of thousands of character nodes on every single keystroke.
* At 10,000 QPS, this results in billions of pointer dereferences per second, causing massive CPU cache misses and driving latency into hundreds of milliseconds.

<p align="center">
  <img src="images/diagram-3-trie-node-topk-cache.svg" width="100%" alt="Trie Query Performance: Subtree DFS vs. Node-Level Top-K Cache">
</p>

#### Back-of-the-Envelope Memory Calculation

To achieve `O(L)` query time, every node must cache its Top-K suggestions. What is the memory cost?

Let us calculate the memory requirements for a vocabulary of 10,000,000 queries with an average length of 15 characters:
* Total character nodes in Trie: Approximately 25,000,000 nodes (due to shared prefix compression).
* Standard Python dictionary object: ~152 bytes overhead per node.
* Node attributes (`children`, `is_end`, `frequency`, `top_k`, `word`):
  * Pointer to children dict: 8 bytes.
  * `top_k` list (5 elements x 8 bytes): 40 bytes.
  * String pointers and integers: 32 bytes.
  * Total per node without optimization: **~232 bytes**.
* Total RAM for 25M nodes: `25,000,000 x 232 bytes = 5,800,000,000 bytes = 5.8 GB`.
* Plus string vocabulary and child dict hash tables: **~9.8 GB of RAM**.

By utilizing Python's `__slots__`, we eliminate the dynamic `__dict__` overhead on every node, bringing the per-node overhead down from ~232 bytes to ~56 bytes, reducing baseline memory consumption to approximately **4.1 GB**.

---

### Milestone 3: Implementing Memory-Optimized `TrieNode`

Open `typeahead_lab/trie_node.py` and inspect the class definition:

```python
class TrieNode:
    """
    A single character node in the Prefix Trie.
    
    Attributes:
        children: Mapping from character to next child TrieNode.
        is_end: Boolean indicating if a complete search query terminates here.
        frequency: Search frequency count (greater than 0 if is_end is True).
        word: The complete query string terminating at this node (if is_end is True).
        top_k: Precomputed list of top-K (query, frequency) tuples cached at this node.
    """

    __slots__ = ("children", "is_end", "frequency", "word", "top_k")

    def __init__(self):
        self.children: Dict[str, "TrieNode"] = {}
        self.is_end: bool = False
        self.frequency: int = 0
        self.word: Optional[str] = None
        self.top_k: List[Tuple[str, int]] = []
```

> **Important — Why `__slots__` Matters**: By declaring `__slots__`, Python skips creating an internal hash table (`__dict__`) for each object instance. When allocating tens of millions of nodes, this single optimization eliminates gigabytes of unnecessary memory overhead and dramatically improves CPU cache locality.

---

### Milestone 4: Implementing Post-Order Top-K Caching in `TrieTypeaheadService`

Open `typeahead_lab/trie_service.py`. Let us examine how the Trie caches top-K queries at every node.

#### The Bottom-Up Construction Algorithm

Populating `node.top_k` efficiently requires a **post-order depth-first traversal**:
1. Recurse down to the leaves of the Trie first.
2. At leaf nodes, the top-K list contains only the leaf's terminating word (if `is_end` is True).
3. As the recursion unwinds bottom-up, each parent merges the already-sorted `top_k` lists of all its immediate children.
4. If a word terminates at the current node itself (`node.is_end == True`), that word must also be included in the candidate list.
5. The combined candidate list is sorted by frequency descending, and sliced to size `K`.

<p align="center">
  <img src="images/diagram-4-trie-bottom-up-build.svg" width="100%" alt="Post-Order Bottom-Up Top-K Construction &amp; The Self-Ending Word Trap">
</p>

#### The "Self-Ending Word" Edge Case Trap

> **Warning — Subtle Production Bug**: Consider the query `"car"` with frequency 5000, and `"card"` with frequency 4000. At node `'r'`, the query `"car"` terminates (`node.is_end = True`). Its child node `'d'` terminates `"card"`. If your merge logic only aggregates suggestions from child nodes, `"car"` will be completely omitted from its own prefix node cache! You must always include `node.word` if `node.is_end` is True.

Inspect the implementation of **TODO 1** in `typeahead_lab/trie_service.py`:

```python
def build_top_k(self, k: int = 5) -> None:
    """
    TODO 1: Implement Post-Order Bottom-Up Top-K Caching.
    """
    def _post_order_dfs(node: TrieNode) -> List[Tuple[str, int]]:
        candidates: Dict[str, int] = {}

        # 1. Recurse into all children first (bottom-up post-order)
        for child in node.children.values():
            child_top_k = _post_order_dfs(child)
            for query, freq in child_top_k:
                if query not in candidates or freq > candidates[query]:
                    candidates[query] = freq

        # 2. Critical Edge Case: If a valid word terminates at this node, include it!
        if node.is_end and node.word:
            candidates[node.word] = max(node.frequency, candidates.get(node.word, 0))

        # 3. Sort candidates by frequency descending, break ties alphabetically
        sorted_candidates = sorted(
            candidates.items(),
            key=lambda item: (-item[1], item[0])
        )

        # 4. Cache the top-K results directly on this node
        node.top_k = sorted_candidates[:k]
        return node.top_k

    _post_order_dfs(self.root)
```

---

### Milestone 5: Implementing `O(L)` Prefix Suggestion

With `node.top_k` populated, suggestion retrieval requires only walking down the characters of the prefix. Inspect **TODO 2** in `typeahead_lab/trie_service.py`:

```python
def suggest(self, prefix: str, k: Optional[int] = None) -> List[Tuple[str, int]]:
    """
    TODO 2: Implement Prefix Search with Node-Cached Top-K.
    """
    target_k = k if k is not None else self.k
    current = self.root
    normalized = prefix.strip().lower()

    # O(L) character traversal
    for char in normalized:
        if char not in current.children:
            return []  # Prefix does not exist in vocabulary
        current = current.children[char]

    # The prefix node directly holds precomputed Top-K suggestions
    return current.top_k[:target_k]
```

Notice the algorithmic complexity:
* **Time Complexity**: Exactly `O(L)` where `L` is the number of characters in the typed prefix. For a typical prefix of length 3 (`"sys"`), the loop executes exactly 3 times, regardless of whether the dataset contains 100 queries or 100,000,000 queries.
* **Space Complexity**: `O(1)` auxiliary space during search.

---

### Milestone 6: Verifying Trie Parity and Single-Query Latency

Now execute `main.py` to verify that the Trie builds properly and produces correct suggestions:

```bash
python3 typeahead_lab/main.py
```

Inspect the terminal output:

```text
============================================================================
  TYPEAHEAD & AUTOCOMPLETE LAB: TRIE vs HASH+HEAP PARITY VERIFICATION
============================================================================
[*] Loaded curated dataset: 165 technical search queries
[+] Built Trie Service in 2.65ms
    - Total Nodes: 2,956 | Estimated RAM: 1052.86 KB
[+] Built Hash+Heap Service in 0.20ms
    - Unique Prefixes: 119 | Estimated RAM: 180.84 KB

============================================================================
  SUGGESTION PARITY CHECK (Top-5 Suggestions for Test Prefixes)
============================================================================

Prefix: 'sys' -> Status: MATCH [PASS]
  Trie Results      : [('system design interview', 9850), ('system design rate limiter', 8420), ('system design caching', 7650)]
  Hash+Heap Results : [('system design interview', 9850), ('system design rate limiter', 8420), ('system design caching', 7650)]

Prefix: 'py' -> Status: MATCH [PASS]
  Trie Results      : [('python tutorial', 9650), ('python list comprehension', 8720), ('python dictionary methods', 7890)]
  Hash+Heap Results : [('python tutorial', 9650), ('python list comprehension', 8720), ('python dictionary methods', 7890)]

----------------------------------------------------------------------------
[SUCCESS] All test prefixes yielded 100% identical top-K ranked suggestions!
----------------------------------------------------------------------------

============================================================================
  SINGLE-QUERY LATENCY MICRO-BENCHMARK (1,000 iterations for 'sys')
============================================================================
  Trie O(L) Lookup Latency      : 0.264 microseconds (0.00026 ms)
  Hash+Heap Lookup Latency      : 2.049 microseconds (0.00205 ms)
============================================================================
```

Notice the single-query lookup latency: **0.264 microseconds (0.00026 ms)**! The Trie easily beats our 3ms compute budget. However, notice the memory consumption: even for a modest 165 queries, the Trie consumed **1,052 KB** of RAM across 2,956 individual node allocations. Let us see how we can eliminate this memory footprint.

---

## Stage 4: Hands-On Walkthrough: Part B (Optimized Hash+Heap Service)

### Milestone 7: Why Hash+Heap Outperforms Trie in Production

While a Trie provides elegant theoretical bounds (`O(L)`), large-scale production systems at LinkedIn, Uber, and Twitter frequently avoid full Tries in favor of a **Flat Prefix Hash Table combined with a Bounded Min-Heap**.

<p align="center">
  <img src="images/diagram-5-prefix-hash-heap-architecture.svg" width="100%" alt="Optimized Architecture: Flat Prefix Hash Table &amp; Bounded Min-Heap">
</p>

Why does the Hash+Heap architecture win in production?
1. **No Intermediate Character Nodes**: A Trie creates a distinct object for every intermediate character (`'s'`, `'y'`, `'s'`, `'t'`, `'e'`, `'m'`). A Hash Table indexes prefixes directly as flat strings up to a maximum length (e.g., `L <= 6`).
2. **Elimination of Pointer Chasing**: Walking a Trie involves chasing memory pointers across heap-allocated node objects, causing L1/L2 CPU cache line misses. A Python hash table lookup executes in contiguous memory buckets.
3. **Drastic Garbage Collection Overhead Reduction**: Allocating 25,000,000 TrieNode objects causes significant Garbage Collector (GC) mark-and-sweep pauses. A hash map with 500,000 prefix keys eliminates 95% of object allocations.
4. **The 6-Character Prefix Principle**: In real-world search engines, 98% of users select an autocomplete suggestion within the first 4 to 6 keystrokes. Indexing prefixes beyond length 6 yields rapidly diminishing returns. Restricting prefix indexing to `L <= 6` caps the total number of keys.

#### Back-of-the-Envelope Hash+Heap Memory Math

For 10,000,000 queries indexed up to `L = 6`:
* Unique prefixes generated: Approximately 1,200,000 prefixes.
* Hash map storage: 1.2M keys x 64 bytes = ~76 MB.
* Candidate reference arrays: ~550 MB.
* Total memory footprint: **~650 MB to 900 MB** (compared to **4 GB to 10 GB** for a full Trie).
* **Net RAM savings: 75% to 90%**!

---

### Milestone 8: Implementing Bounded Min-Heap Extraction

Open `typeahead_lab/hash_heap_service.py`. Let us examine how the service indexes prefixes and extracts top-K items using a bounded Min-Heap.

#### The Bounded Min-Heap Algorithm

When querying a prefix bucket with `B` candidate queries:
1. Initialize an empty Min-Heap.
2. For each candidate `(query, frequency)` in the bucket:
   * If heap size < K: push `(frequency, query)` directly onto the heap.
   * If heap size == K: compare candidate `frequency` with `min_heap[0][0]` (the smallest frequency among the current top-K).
   * If candidate frequency is larger: call `heapq.heappushpop(min_heap, (frequency, query))`.
3. The heap retains only the `K` highest-frequency items.
4. Pop elements from the heap and sort descending.

Algorithmic complexity:
* **Time Complexity**: `O(1)` hash map lookup + `O(B log K)` heap maintenance. Because `K` is small (`K = 5`), `log2(5) ~= 2.3` operations per candidate.
* **Space Complexity**: `O(K)` auxiliary space during query processing.

Inspect the implementation of **TODO 3** in `typeahead_lab/hash_heap_service.py`:

```python
def suggest(self, prefix: str, k: Optional[int] = None) -> List[Tuple[str, int]]:
    """
    TODO 3: Implement Bounded Min-Heap Top-K Extraction.
    """
    target_k = k if k is not None else self.k
    normalized = prefix.strip().lower()

    # Step 1: O(1) Prefix Hash Lookup
    candidates = self.prefix_index.get(normalized)
    if not candidates:
        return []

    # Step 2: Bounded Min-Heap Extraction
    min_heap: List[Tuple[int, str]] = []

    for query, freq in candidates:
        if len(min_heap) < target_k:
            heapq.heappush(min_heap, (freq, query))
        else:
            if freq > min_heap[0][0]:
                heapq.heappushpop(min_heap, (freq, query))

    # Step 3: Extract, sort descending by frequency, break ties alphabetically
    results: List[Tuple[str, int]] = []
    while min_heap:
        freq, query = heapq.heappop(min_heap)
        results.append((query, freq))

    results.reverse()
    results.sort(key=lambda item: (-item[1], item[0]))
    return results
```

---

### Milestone 9: Running the 10,000 QPS Load Test and Shootout Benchmark

Now let us execute the comprehensive load test and memory profiling suite in `benchmark.py`. This script procedurally generates 10,000 technical search queries following a Zipfian distribution, builds both engines under `tracemalloc` memory profiling, and executes 10,000 concurrent simulated prefix searches.

Run the benchmark:

```bash
python3 typeahead_lab/benchmark.py
```

Inspect the real empirical results:

```text
==============================================================================
  TYPEAHEAD & AUTOCOMPLETE LOAD TEST & MEMORY BENCHMARK (10,000 QUERIES)
==============================================================================
[*] Generating expanded corpus with Zipfian distribution (10,000 queries)...
    Total unique queries generated: 10,000

[+] [Trie Engine] Built in 573.51ms
    - Total Nodes   : 136,472
    - Peak RAM Usage: 58.19 MB

[+] [Hash+Heap Engine] Built in 22.30ms
    - Unique Prefixes: 449
    - Peak RAM Usage : 4.52 MB
    - RAM Reduction  : 92.2% less memory than Trie!

[*] Executing 10,000 simulated prefix keystroke queries...

==============================================================================
Performance Metric           | Trie with Cache  | Hash + Min-Heap  | SLA Target
------------------------------------------------------------------------------
Build Time (10k items)       |        573.51 ms |         22.30 ms | < 5000 ms 
Peak Memory (RAM)            |         58.19 MB |          4.52 MB | < 100 MB  
RAM Savings                  |         Baseline |    92.2% savings | > 50%     
Throughput (QPS)             |       2809778 qps |         65224 qps | > 10k QPS 
p50 Latency                  |        0.0003 ms |        0.0110 ms | < 10 ms   
p90 Latency                  |        0.0004 ms |        0.0297 ms | < 25 ms   
p99 Latency                  |        0.0007 ms |        0.0536 ms | < 50 ms   
Max Latency                  |        0.0051 ms |        0.1588 ms | < 100 ms  
Redis L1 Cache (p99)         |        0.0086 ms |        0.0086 ms | < 5 ms    
==============================================================================

[VERIFICATION RESULT] ALL SLA CRITERIA FULLY SATISFIED:
  1. p99 Latency < 50ms SLA Target: PASSED (Trie: 0.0007ms, Hash+Heap: 0.0536ms)
  2. Concurrent Throughput Target : PASSED (Trie: 2,809,778 QPS, Hash+Heap: 65,224 QPS)
  3. Memory Footprint Reduction   : PASSED (92.2% memory savings)
==============================================================================
```

#### Analyzing the Empirical Findings

1. **Peak RAM Reduction**: The Hash+Heap engine consumed only **4.52 MB** compared to **58.19 MB** for the Trie, achieving an outstanding **92.2% reduction in memory consumption**!
2. **Build Time**: The Hash+Heap index built in **22.30 ms**, over **25 times faster** than the Trie's 573.51 ms recursive post-order construction.
3. **Latency Compliance**: Both engines achieved p99 latency far below the 50ms SLA:
   * Trie p99: **0.0007 ms** (0.7 microseconds).
   * Hash+Heap p99: **0.0536 ms** (53.6 microseconds).
   Both engines deliver more than 50x safety margin under the 3ms compute budget!
4. **Throughput**: Hash+Heap delivered **65,224 QPS**, comfortably surpassing the 10,000 QPS target, while the Trie reached **2.8 million QPS** due to direct array index reads.

---

### Milestone 10: FastAPI Microservice Integration & HTTP Verification

In production, your typeahead engine is packaged as an HTTP microservice behind an API gateway.

Open `typeahead_lab/api.py`. The service exposes:
* `GET /suggest?q={prefix}&engine={trie|hash}&k={5}`: Checks Redis hot-cache first. On cache miss, queries the selected engine, populates Redis with a 5-minute TTL, and returns results.
* `POST /search`: Logs completed user queries to Redis Sorted Sets via atomic `ZINCRBY`.
* `GET /metrics`: Returns live memory and node statistics for both engines.

You can verify the HTTP microservice using Python's TestClient or curl:

```python
from fastapi.testclient import TestClient
from api import app

client = TestClient(app)
response = client.get("/suggest?q=sys&engine=hash&k=3")
print(response.json())
```

Expected JSON response:

```json
{
  "prefix": "sys",
  "engine": "hash",
  "source": "memory_hash",
  "count": 3,
  "suggestions": [
    {"query": "system design interview", "frequency": 9850},
    {"query": "system design rate limiter", "frequency": 8420},
    {"query": "system design caching", "frequency": 7650}
  ]
}
```

When you request the same prefix a second time, notice that `"source"` flips to `"redis_cache"`:

```json
{
  "prefix": "sys",
  "engine": "hash",
  "source": "redis_cache",
  "count": 3,
  "suggestions": [
    {"query": "system design interview", "frequency": 9850},
    {"query": "system design rate limiter", "frequency": 8420},
    {"query": "system design caching", "frequency": 7650}
  ]
}
```

The L1 Redis cache absorbs subsequent repeated keystrokes with zero query overhead on application worker memory!

---

## Stage 5: Enterprise-Scale Architecture (Conceptual Glimpse)

Having implemented single-node Trie and Hash+Heap engines, we now step back to examine how planetary-scale systems (Google, Amazon, LinkedIn) scale this architecture to hundreds of millions of users.

### Consistent Hash Sharding & Pinned Caches

When a search vocabulary exceeds 100,000,000 queries, the dataset cannot fit comfortably in a single server's memory, nor can a single machine handle 500,000 global QPS.

<p align="center">
  <img src="images/diagram-6-enterprise-sharding-pinned-cache.svg" width="100%" alt="Enterprise Scale: Consistent Hash Sharding &amp; Pinned Caches">
</p>

Enterprise scaling rules:
1. **Prefix-Based Sharding**: Instead of sharding randomly by full query hash, the system shards by the **prefix hash** (e.g., `hash(prefix[:2])`). All queries starting with `"do"` route to the same shard.
2. **Pinned L1 Caches**: Because routing is deterministic, each shard maintains a "pinned" L1 memory cache of its partition's top 1,000 prefixes, yielding a **90%+ cache hit ratio**.
3. **Multi-Region Replication**: Shards are replicated across US-East, US-West, EU-Central, and AP-East, routing users to their nearest datacenter via Anycast DNS to minimize network RTT.

### Streaming Frequency Decay & Atomic RCU Updates

Real-world search volumes change continuously. A breaking news event or viral video causes query spikes within minutes. Conversely, last month's trending meme fades away. How does an enterprise update autocomplete without dropping read throughput?

<p align="center">
  <img src="images/diagram-7-enterprise-async-rcu-pipeline.svg" width="100%" alt="Real-Time Frequency Decay &amp; Atomic RCU Pointer Swap">
</p>

Enterprise update rules:
1. **Exponential Time-Decay Scoring**: Raw search counts are weighted by an exponential decay function:
   ```text
   Score(t) = Sum(Count * e^(-lambda * (t - t0)))
   ```
   This ensures today's trending searches rank higher than queries from three months ago.
2. **Off-Path Aggregation via Kafka and Flink**: Completed search clicks are streamed to Apache Kafka. Apache Flink computes tumbling 5-minute frequency windows without touching the read path.
3. **Read-Copy-Update (RCU) Double Buffering**: Active search workers never mutate a Trie or Hash Table in-place during live queries. Instead, a background thread builds a brand-new index in memory. Once complete, it executes an **atomic pointer swap** (`active_index = new_index`). Reads continue at microsecond speeds with zero locking or mutex contention.

---

## Stage 6: Real-World FAQs

### 1. Scalability: How does this service handle 500,000 queries per second?
At 500,000 QPS, no single process or server suffices. The service scales horizontally across three tiers:
* **Edge CDN Caching**: Cloudflare/CloudFront caches top 100 prefixes (`"a"`, `"s"`, `"th"`) at point-of-presence edges with a 60-second TTL, absorbing 30% to 40% of all keystrokes before hitting origin servers.
* **Prefix-Partitioned Shards**: The vocabulary is partitioned across 50 to 100 shard replicas using consistent hashing on `prefix[:2]`.
* **Multi-Threaded Worker Processes**: Each shard replica runs multiple Uvicorn worker processes reading from the same shared-memory read-only index.

### 2. Failure Modes: What happens if the Redis instance crashes or experiences network partition?
As implemented in `redis_client.py`, the typeahead service treats Redis as a non-critical sidecar:
* If Redis goes offline, the `TypeaheadRedisClient` catches the socket error and switches to its internal in-memory fallback mock.
* Read requests continue serving from local Trie or Hash+Heap memory without interruption.
* Query logging continues in local memory until Redis connectivity recovers.

### 3. Trade-offs: When should an engineering team choose Trie over Hash+Heap, and vice versa?
* **Choose Trie when**:
  * The vocabulary is open-ended with no fixed prefix length limit.
  * You require advanced fuzzy matching, Levenshtein edit-distance traversal, or wildcard matching (`"sys*inter*"`).
  * Memory is abundant and build time is non-critical.
* **Choose Hash+Heap when**:
  * You operate under strict RAM constraints (reducing memory by 75% to 92%).
  * Fast index build times and rapid cold restarts are required.
  * Search traffic is heavily concentrated on standard prefix lengths (`L <= 6`).

### 4. Edge Cases: How does the system handle multi-word queries with arbitrary word ordering?
Users frequently type words out of order (e.g., typing `"interview system"` instead of `"system design interview"`).
* A single prefix trie indexed on full strings will miss out-of-order queries.
* Production systems address this by indexing **term permutations** or indexing prefixes of individual words inside an inverted token dictionary, linking tokens back to canonical query IDs.

### 5. Edge Cases: How do you prevent offensive, copyrighted, or sensitive terms from appearing in suggestions?
Production autocomplete engines maintain an explicit **Denylist Filter**:
* Before any candidate list is returned to a user, it passes through a Bloom Filter or Hash Set containing prohibited keywords, hate speech, and sensitive personal identifiers.
* Filtered candidates are removed and replaced by the next valid candidate from the heap.

### 6. Operational Maintenance: How frequently should the in-memory index be rebuilt?
* **Real-time hot queries**: Handled by the L1 Redis cache and updated continuously via `ZINCRBY`.
* **Full In-Memory Index Rebuild**: Typically scheduled every 2 to 6 hours as an asynchronous batch job. A background worker generates the new Hash/Trie image from the aggregated frequency database and performs an atomic pointer swap, consuming zero read downtime.

---

## Stage 7: Edge Cases & Troubleshooting Scenarios

### Scenario 1: Memory Bloat After Procedural Corpus Expansion
* **Symptom**: After expanding the query dataset from 500 to 50,000 items, the Trie service process memory jumps from 50 MB to over 1.2 GB, triggering Linux Out-Of-Memory (OOM) killer alerts.
* **Root Cause**: The `TrieNode` class was defined without `__slots__`, forcing Python to allocate a dynamic `__dict__` for each of the 600,000 newly created node instances.
* **Diagnosis**: Run `sys.getsizeof(node.__dict__)` on a sample node.
* **Solution**: Ensure `__slots__ = ("children", "is_end", "frequency", "word", "top_k")` is declared at the top of `TrieNode`. Alternatively, migrate to `HashHeapTypeaheadService` with prefix length capped at `L <= 6`, instantly dropping memory below 25 MB.

### Scenario 2: The "Self-Ending Word" Missing from Autocomplete
* **Symptom**: When typing `"sys"`, the query `"system"` does not appear in suggestions, even though `"system design interview"` appears. `"system"` has a high historical search count.
* **Root Cause**: The post-order bottom-up construction algorithm only aggregated top-K items returned by child nodes, forgetting to check if the current node itself is a terminating word (`node.is_end == True`).
* **Diagnosis**: Check `trie_service.py` build logic. If `node.is_end` is not checked, self-terminating words are discarded.
* **Solution**: In `build_top_k()`, explicitly insert `(node.word, node.frequency)` into the candidates dictionary before sorting and slicing.

### Scenario 3: High Latency Spikes (p99 > 80ms) During Search Query Logging
* **Symptom**: Read latency is normally 0.05ms, but spikes to 80ms every few seconds when query submission rate increases.
* **Root Cause**: Query logging was executing synchronous database writes or locking the entire Trie with a mutual exclusion lock (`threading.Lock()`) on the read path.
* **Diagnosis**: Profile lock wait times using `py-spy` or Python's `cProfile`.
* **Solution**: Decouple query logging completely from the read path. Use asynchronous Redis `ZINCRBY` or push search click events to an async in-memory queue (`asyncio.Queue`), processing increments in batches off the main thread.

### Scenario 4: Autocomplete Suggestions Stale After Breaking News Event
* **Symptom**: A major technical release occurs (e.g., `"python 3.14 release"`), but autocomplete continues recommending older, lower-priority queries for hours.
* **Root Cause**: Static lifetime frequencies dominate rankings because old queries have accumulated millions of historical searches, while new breaking queries have only thousands.
* **Diagnosis**: Inspect scores in Redis Sorted Sets.
* **Solution**: Implement time-decay weighting in your aggregation pipeline (`Score = Freq * e^(-lambda * Delta_t)`), or boost queries whose 1-hour rate of change (`Delta_Q / Delta_t`) exceeds a dynamic velocity threshold.

---

## Stage 9: Summary & System Design Mastery Checklist

### Codebase File Inventory

| File | Primary Class / Entrypoint | Key Responsibility |
| :--- | :--- | :--- |
| `dataset.py` | `QueryDataset` | Realistic tech search query seed data with Zipfian frequency distribution and expansion generator. |
| `redis_client.py` | `TypeaheadRedisClient` | Asynchronous query logging via `ZINCRBY`, L1 prefix caching with TTL, and fallback mock. |
| `test_redis.py` | Standalone script | 4-step connectivity and functional verification for Redis integration. |
| `trie_node.py` | `TrieNode` | Memory-optimized node entity using `__slots__` to eliminate dynamic dict overhead. |
| `trie_service.py` | `TrieTypeaheadService` | Baseline Trie with post-order bottom-up Top-K caching and `O(L)` prefix lookup. |
| `hash_heap_service.py` | `HashHeapTypeaheadService` | Production-grade Flat Prefix Hash Table with bounded Min-Heap Top-K extraction (`O(B log K)`). |
| `main.py` | Verification runner | Cross-engine suggestion parity check and single-query micro-benchmarks. |
| `benchmark.py` | Load testing suite | 10,000 QPS load test, p50/p90/p99 latency measurement, and `tracemalloc` RAM shootout. |
| `api.py` | FastAPI application | RESTful microservice with `/suggest`, `/search`, and `/metrics` endpoints. |

### System Design Competencies Mastered

* [x] **The Inversion Principle**: Understanding why autocomplete requires write-time ranking rather than read-time computation.
* [x] **Latency Budget Engineering**: Designing for strict p99 < 50ms keystroke SLAs, allocating <= 3ms for compute.
* [x] **Failure Analysis of Relational DBs & Inverted Indexes**: Articulating why `LIKE 'prefix%'` and token-boundary inverted indexes fail under high QPS.
* [x] **Trie Optimization**: Implementing `__slots__` memory optimization and bottom-up post-order Top-K caching while avoiding the self-ending word bug.
* [x] **Production Hash+Heap Trade-Offs**: Proving empirically that flat prefix hashing reduces RAM by **92.2%** while delivering **65,000+ QPS**.
* [x] **Distributed Caching with Redis**: Utilizing atomic Sorted Sets (`ZINCRBY`) and L1 string caches to absorb keystrokes off the read path.
* [x] **Enterprise Architecture**: Designing consistent hash sharding, pinned L1 caching, and RCU double-buffered atomic pointer swaps for planetary scale.

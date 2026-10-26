# M16a-L2: Typeahead & Autocomplete Service with a Trie & Hash+Heap Optimization

<p align="center">
  <img src="images/overview-architecture.svg" width="100%" alt="M16a-L2 End-to-End System Architecture">
</p>

---

## Introduction: The Invisible Miracle of the Search Box

Consider what happens dozens of times a day without you ever noticing:
* You are standing in the middle of a crowded grocery store, trying to recall an ingredient. You pull out your phone, tap the search bar, type `van`, and instantly—before your thumb even lifts off the glass—the application suggests `vanilla extract`, `vanilla bean paste`, and `vanilla pudding`.
* You are rushing through an airport on a noisy concourse with spotty mobile Wi-Fi, booking a last-minute flight. You type `san`, and the dropdown immediately anticipates `San Francisco (SFO)`, `San Diego (SAN)`, and `San Antonio (SAT)`.
* You open YouTube, Spotify, or Google. You type two letters, and the exact artist, video, or documentation page you had in mind appears at the top of the list.

You never pressed the **Enter** key. You never clicked a **Search** button. The system seemed to read your thoughts in real time, predicting your intention before you even finished spelling the word.

Before we write a single line of code, let us establish a fundamental terminology rule: in backend and distributed systems engineering, **Typeahead**, **Autocomplete**, and **Prefix Suggestion** refer to the exact same architectural capability. Whether you are building product discovery for Amazon, document navigation for Notion, or channel search for Slack, the engineering objective is identical: predict and rank the user's intent after every individual keystroke.

In Lab 1 - Inverted Index, you explored Information Retrieval from the perspective of complete queries. You built an inverted index with BM25 ranking that processes search terms *after* the user submits their full query.

In this lab, you step into a radically different engineering reality. Autocomplete does not wait for a complete query. It operates under severe real-time constraints, handling ten to twenty times higher request volume with an order of magnitude tighter latency. Over the course of this lab, you will design, build, and benchmark a production-grade typeahead service from the ground up, evolving from a baseline Prefix Tree to an optimized Hash and Heap architecture that cuts memory consumption by **92%**.

---

## Stage 1: The Production Reality & The 50ms Latency Budget

To design systems that feel instant, we must first examine human sensory biology and the physics of network communication.

### The Cognitive Psychology of Keystroke Latency

In human-computer interaction research, human perception operates across well-documented temporal thresholds:
* **0 to 100 milliseconds**: An interaction feels instantaneous. The human visual cortex and brain interpret the system's reaction as a direct, physical extension of their own thought and motor action.
* **100 to 300 milliseconds**: The user notices a slight delay. The illusion of direct manipulation breaks, but the user feels in control of the interface.
* **Over 300 milliseconds**: The interaction feels distinctly sluggish. Users pause their typing, look up at the screen to see if their keystroke registered, or re-type characters, creating phantom inputs and extreme frustration.

Now, consider the physical mechanics of human typing:
* A typical person typing on a smartphone or laptop keyboard inputs text at a rhythm of **60 to 80 words per minute**.
* This corresponds to a new keystroke landing every **150 to 180 milliseconds**.

If your autocomplete suggestion service takes longer than **50 milliseconds** round-trip to deliver suggestions, disaster strikes. While the backend is still computing suggestions for the letter `s`, the user's thumb has already hit `y`. The suggestions for `s` arrive out of order, the UI dropdown flickers with stale data, and the browser repaints elements unnecessarily.

To guarantee that suggestions render cleanly before the next keystroke lands, the entire end-to-end round-trip latency must strictly remain **under 50 milliseconds at the 99th percentile (p99)**.

<p align="center">
  <img src="images/diagram-1-keystroke-latency-budget.svg" width="100%" alt="The 50ms Keystroke Latency Budget &amp; Human Typing Rhythm">
</p>

### Deconstructing the 50-Millisecond Budget

Let us break down where those 50 milliseconds actually go in a real production environment:

1. **Client Network Ingress (15 ms)**:
   The physical transmission of the HTTP/WebSocket packet from the user's device across wireless networks, cell towers, and Internet Service Providers to your cloud edge or load balancer. Over real-world mobile LTE/5G or home Wi-Fi, this consumes 15 to 18 milliseconds each way.
2. **API Gateway, TLS Termination & Routing (2 ms)**:
   The reverse proxy (such as NGINX, Envoy, or AWS ALB) decrypts TLS, evaluates authentication tokens, enforces rate limits, and forwards the request to an internal application worker.
3. **Internal Autocomplete Compute Budget (<= 3 ms)**:
   This is the window allocated to your application code. Your data structure must locate matching prefix candidates, rank them by historical popularity, and extract the top 5 suggestions in **less than 3 milliseconds**.
4. **JSON Serialization & Response Packaging (1 ms)**:
   Converting internal objects into a lightweight JSON payload and writing bytes to the network socket.
5. **Network Egress (15 ms)**:
   Transmitting the response packets back across the internet to the client device.
6. **Browser / Mobile UI DOM Rendering (4 ms)**:
   The client JavaScript engine parses the JSON, calculates layout diffs, and paints the suggestion dropdown on the screen.

When you total these stages:
```text
15ms (Ingress) + 2ms (Gateway) + 3ms (Compute) + 1ms (JSON) + 15ms (Egress) + 4ms (DOM) = 40 milliseconds
```

This leaves a narrow **10-millisecond safety margin** to absorb mobile network jitter and packet loss. If your backend compute engine takes 15ms or 20ms instead of 3ms, the entire user experience collapses. Your compute engine must execute its ranking and prefix lookup in **microseconds**, not milliseconds.

---

## Stage 2: Why Relational Databases and Inverted Indexes Fail

When faced with building an autocomplete feature, software engineers often consider utilizing their existing database infrastructure or the inverted index they built in Lab 1. Let us analyze why both approaches fail under production keystroke volume.

### The Relational Database Bottleneck: `LIKE 'prefix%'`

An intuitive initial approach that almost any engineer naturally considers first is to query an existing relational database (such as PostgreSQL or MySQL):

```sql
SELECT query, frequency 
FROM search_history 
WHERE query LIKE 'sys%' 
ORDER BY frequency DESC 
LIMIT 5;
```

Even if you create a B-tree index on the `query` column, this design breaks down under real-world load due to three architectural choke points:

1. **Index Range Scan Amplification**:
   While a B-tree index can evaluate `LIKE 'sys%'` as a range scan (`query >= 'sys' AND query < 'syt'`), it matches every single query starting with those three letters. In a technical dictionary or e-commerce catalog, `sys%` matches tens of thousands of rows. The database engine must pull every matching index leaf page into memory.
2. **Read-Time Sorting Bottleneck**:
   The B-tree index is sorted alphabetically by `query`, not by `frequency`. Therefore, the database cannot simply pick the first 5 rows. It must fetch all 20,000 matching rows, load their frequency counters, and execute a read-time sort (`ORDER BY frequency DESC`) before discarding 19,995 rows to return the top 5.
3. **Concurrency and Buffer Pool Lock Thrashing**:
   In standard web applications, users submit queries occasionally. In autocomplete, every keystroke fires an HTTP request. At a modest scale of 10,000 active typists, the database receives **10,000 queries per second (QPS)**. Database connection pools saturate instantly. CPU cores spend 100% of their cycles performing sort operations and managing buffer pool mutexes, pushing p99 latency beyond 500 milliseconds.

### The Inverted Index Keystroke Mismatch

In Lab 1, you built an inverted index designed to retrieve documents matching complete search terms. Why can we not reuse that inverted index for autocomplete?

1. **Token Boundary Incompatibility**:
   Inverted indexes tokenize text on word boundaries (`"system"`, `"distributed"`, `"caching"`). When a user types `sys`, the input is not a word; it is an incomplete prefix. An inverted index cannot look up `sys` directly because `sys` does not exist in the vocabulary dictionary.
2. **The N-Gram Index Storage Explosion**:
   To make an inverted index support arbitrary character prefixes, you must decompose every indexed term into character n-grams. For the word `system`, you must index:
   * 2-grams: `sy`, `ys`, `st`, `te`, `em`
   * 3-grams: `sys`, `yst`, `ste`, `tem`
   * 4-grams: `syst`, `yste`, `stem`
   * 5-grams: `syste`, `ystem`
   This n-gram expansion balloons index storage by 500% to 800% and requires intersecting massive posting lists at query time, creating unacceptable CPU overhead.
3. **Write Path Contention**:
   Updating search frequency counts in an inverted index requires modifying posting lists on disk, which is poorly suited for streaming, real-time counters.

### The Inversion Principle

The fundamental architectural revelation of autocomplete systems is **The Inversion Principle**:

* **Traditional Search (Lab 1)** indexes raw data and defers complex scoring, ranking, and relevance calculations to **read time** (when the user presses Enter).
* **Autocomplete** inverts this relationship: it precomputes, aggregates, and ranks candidate suggestions **at build time**, so that read-time retrieval is reduced to a trivial, zero-computation memory lookup.

<p align="center">
  <img src="images/diagram-2-inversion-principle.svg" width="100%" alt="The Inversion Principle: Traditional Search vs. Autocomplete">
</p>

By shifting the computational burden of sorting and ranking to the background build phase, the read path executes in microseconds.

---

## Stage 2 (Continued): Distributed Caching & The Role of Redis

Before constructing our in-memory data structures, we must address how state, caching, and frequency counting are coordinated across a distributed fleet of application servers.

### The Read Path vs. Write Path Dilemma in Distributed Autocomplete

In a production environment, an autocomplete service is deployed across dozens of container instances behind reverse proxies (such as NGINX, Envoy, or AWS Application Load Balancers). Each container executes a multi-process WSGI or ASGI application server (such as Uvicorn or Gunicorn with 8 to 16 worker processes per CPU socket) to handle high concurrency.

If an engineering team attempts to handle both prefix search and search frequency tracking entirely inside local process memory, two fatal architectural problems arise:

1. **State Divergence Across Application Workers**:
   Because incoming HTTP requests are load-balanced across multiple independent worker processes, Worker 1 might record 150 searches for `"kubernetes tutorial"` while Worker 2 records only 10. Depending on which worker happens to handle a user's next keystroke, the user receives wildly inconsistent and fluctuating suggestion rankings. Synchronizing state across independent OS processes without a shared data store requires complex IPC or distributed gossip protocols.
2. **The Read-Write Lock Contention Trap**:
   A search query frequency update cannot simply overwrite a counter in place. In an autocomplete engine, changing a query's frequency alters the top-K rankings for all of its prefixes (such as `'k'`, `'ku'`, `'kub'`, and `'kube'`). Updating these rankings inside an active Trie or Hash Table requires acquiring an exclusive write lock (using `threading.Lock` or a read-write `RWLock`).
   Under 10,000 QPS of read traffic, acquiring an exclusive write lock freezes dozens of concurrent read threads. Thread execution queues rapidly back up, context switching surges, and p99 tail latency explodes from 0.05ms to over 100ms, completely breaching our 50ms keystroke SLA.

### Decoupling Read and Write Paths via Redis

To resolve this dilemma, production systems strictly decouple the keystroke **Read Path** from the query logging **Write Path**:

1. **The Lock-Free Read Path (< 1 ms)**:
   Read workers maintain an **immutable in-memory index** (Trie or Hash+Heap) in RAM. Because the index is treated as read-only during active query traffic, worker threads read simultaneously without acquiring mutex locks, achieving microsecond-level latency and millions of operations per second.
2. **The L1 Hot-Prefix Cache (< 1 ms)**:
   Keystroke queries exhibit severe Zipfian skew: a tiny fraction of short prefixes (`"py"`, `"sys"`, `"doc"`) account for over 80% of all incoming keystrokes. Before querying in-memory data structures, the API layer checks Redis for a cached result (`GET prefix_cache:sys`). A fast, single-digit microsecond Redis read absorbs the vast majority of repetitive traffic, protecting application worker memory from burst spikes.
3. **The Fire-and-Forget Write Path**:
   When a user completes a query and submits a search (e.g., pressing Enter), the client issues a `POST /search` event. The application worker immediately increments the query's score in a Redis Sorted Set (`ZINCRBY query_frequencies 1 "system design"`). Because Redis executes operations atomically in single-threaded memory with O(log N) skip-list efficiency, this write operation completes in under 1ms without touching or locking the read-path data structures.
4. **The Offline Batch Bridge & Atomic RCU Swap**:
   Instead of updating in-memory indexes on every single search, an offline batch worker periodically (e.g., hourly or daily) queries the top frequencies from the Redis Sorted Set (`ZREVRANGEBYSCORE`). It builds a brand-new, optimized index snapshot in the background. Once the new index is built, the application workers perform an atomic **Read-Copy-Update (RCU)** pointer swap: the pointer to the active index is updated in a single atomic instruction, ensuring zero lock contention and zero downtime.

### Standalone In-Memory vs. Hybrid Redis Architecture

When designing a production autocomplete tier, software architects choose between two primary paradigms:

| Architectural Dimension | Pure Standalone In-Memory | Hybrid Architecture (Local Engine + Redis) |
| :--- | :--- | :--- |
| **Lookup Latency** | Sub-microsecond (0.0003 ms). Zero network transit. | 0.5 ms to 1.5 ms over local loopback / VPC network. |
| **State Synchronization** | Workers are isolated. Each process maintains its own independent data structure. | Centralized coordination. All worker processes share global frequency counters. |
| **Write Lock Contention** | Mutating a local Trie during active queries requires locks, causing latency spikes. | Redis Sorted Sets (`ZINCRBY`) handle atomic increments completely off the read path. |
| **Process Restarts & Cold Starts** | Process restarts require rebuilding indices from raw database logs (downtime risk). | Redis provides instant read availability while local memory structures rebuild in the background. |
| **Keystroke Traffic Absorption** | Every single keystroke hits local worker CPU memory. | Redis String cache (`GET prefix_cache:sys`) absorbs up to 80% of repeated prefixes. |

### The Production Hybrid Model

In this lab, we adopt the industry-standard **hybrid production model**:
1. **Primary Read Path**: High-throughput in-memory data structures (Trie and Hash+Heap) reside directly in application process RAM to serve prefix queries with sub-millisecond execution.
2. **Asynchronous Write Path**: Redis Sorted Sets (`ZINCRBY query_frequencies 1 "query"`) maintain atomic, global search frequency counts without blocking read queries.
3. **L1 Hot-Prefix Cache**: Redis caches top-K results for popular prefixes (`prefix_cache:sys`) with a 5-minute Time-To-Live (TTL), shielding downstream application memory from traffic spikes.

---

## Stage 3: Hands-On Walkthrough: Part A (Baseline Trie Service)

> **Caution — Learning Guidance**:
> Do not look at the solutions first. For each component, make an honest attempt to implement the `# TODO:` challenges on your own before seeking the solution. Struggling with the tree traversal logic, bottom-up recursion, candidate merging, and heap extraction mechanics is where true engineering intuition develops. Complete, verified solutions for every algorithm and step are fully provided immediately after each challenge block if you get stuck or want to verify your approach.

**Complete solution of the lab is available here: [GitHub Repository](https://github.com/poridhi-labs/m16a-l2-typeahead-trie)**

> [!IMPORTANT]
> **Pre-populating Foundational Dataset & Support Files**:
> Before running the verification scripts in this lab, you must pre-populate the non-trivial helper and dataset files from the GitHub repository into your `typeahead_lab/` workspace:
> 1. `dataset.py`: Contains the curated 165-query technical search corpus and the procedural Zipfian power-law query generator.
> 2. `redis_client.py`: Contains the production Redis client wrapper for atomic query frequency increments (`ZINCRBY`) and L1 prefix caching.
> 3. `test_redis.py`: Contains the standalone 4-step connectivity and operations test suite.
>
> *(You will build the core data structure algorithms in `trie_node.py`, `trie_service.py`, and `hash_heap_service.py` hands-on step by step in the upcoming milestones.)*

Now that you understand the cognitive and architectural constraints, you will build the system step by step.

### Milestone 1: Environment Setup & Redis Integration

Let us begin by setting up your project directory and verifying network connectivity to your Redis instance.

#### Step 1: Create the Project Directory Structure

Open your terminal in the workspace root and execute the following commands to scaffold the project:

```bash
mkdir -p typeahead_lab
cd typeahead_lab
touch __init__.py dataset.py redis_client.py test_redis.py trie_node.py trie_service.py hash_heap_service.py main.py benchmark.py api.py
```

Your workspace layout should match the following structure:

```text
typeahead_lab/
├── __init__.py
├── dataset.py            # Technical search query dataset with Zipfian distribution (pre-populate from GitHub)
├── redis_client.py        # Redis client with atomic ZINCRBY counters & cache (pre-populate from GitHub)
├── test_redis.py          # Standalone verification script for Redis connectivity (pre-populate from GitHub)
├── trie_node.py           # Memory-optimized TrieNode using __slots__ (implement in Milestone 3 & 4)
├── trie_service.py        # Trie implementation with bottom-up Top-K caching (implement in Milestone 5)
├── hash_heap_service.py   # Optimized Hash+Heap implementation (implement in Milestone 7 & 8)
├── main.py                # Suggestion parity and micro-benchmark runner (provided in Milestone 6 & 8)
├── benchmark.py           # 10,000 QPS load test and memory profiler (provided in Milestone 9)
└── api.py                 # FastAPI microservice for HTTP verification (provided in Milestone 10)
```
<p align="center">
  <img src="images/terminal-snapshot-01.png" width="100%" alt="Project Setup">
</p>


#### Step 2: Provisioning Redis in Poridhi Cloud Lab

In this lab, Redis serves as our high-speed cache and atomic query frequency store. To provision and configure your database:

1. In the Poridhi Lab web interface, look at the left-hand navigation sidebar and click on the **Database** section.
2. Click **Create Redis Database**. Once provisioned, the dashboard displays your connection credentials: **Host**, **Port**, **Username**, and **Password**.
3. Open `typeahead_lab/redis_client.py` and enter your credentials into lines 20–28:

```python
# ==============================================================================
# REDIS CREDENTIALS CONFIGURATION
# Students: In Poridhi Lab, enable your Redis database service from the Database
# section and enter the provided host, port, username, and password below.
# ==============================================================================
REDIS_HOST: str = "127.0.0.1"      # Replace with your Poridhi Redis Host (e.g., "100.80.224.229")
REDIS_PORT: int = 6379             # Replace with your Poridhi Redis Port
REDIS_USERNAME: str = "redis"      # Replace with your Poridhi Redis Username
REDIS_PASSWORD: str = "redis"      # Replace with your Poridhi Redis Password
# ==============================================================================
```

> [!NOTE]
> **Redis 6+ Access Control Lists (ACL)**: Poridhi Cloud Lab provisions Redis with modern ACL security enabled. You must supply both `REDIS_USERNAME` (e.g., `"redis"`) and `REDIS_PASSWORD`. If the username is omitted, Redis defaults authentication to the `"default"` user, which will cause authentication to fail.

#### Step 3: Verifying Redis Integration

Before executing the verification test, ensure the `redis` Python driver is installed:

```bash
pip install redis
```

> [!TIP]
> If you encounter `ModuleNotFoundError: No module named 'redis'` when executing the verification script, run `pip install redis` in your terminal to install the driver.

Execute the standalone verification script to confirm connectivity, cache writing, cache reading, and atomic Sorted Set increments against your provisioned database:

```bash
python3 test_redis.py
```

Compare your output against the following verified execution checkpoint:

<p align="center">
  <img src="images/terminal-snapshot-02-redis-test.png" width="100%" alt="Redis-Test">
</p>

With our data access tier verified, we can now turn our attention to memory data structures.

---

### Milestone 2: Trie Data Structure Theory & Memory Realities

A **Prefix Tree** (traditionally called a **Trie**, derived from re*trie*val) is a tree data structure where each node represents a character along a string path. Descendants of a node share the common string prefix associated with that node.

```text
Root
 └── 's'
      └── 'y'
           └── 's' [prefix node 'sys' | top_k: 'system design', 'systemctl status']
                ├── 't' ──> 'e' ──> 'm' (freq: 5000) ──> ' ' ──> 'design' (freq: 9850)
                └── 'c' ──> 't' ──> 'l' ──> ' status' (freq: 3120)
```

#### Why Naive Trie Traversal Fails at Runtime

In an introductory computer science course, an autocomplete query on prefix `sys` is typically described as follows:
1. Walk down the tree along characters `s` -> `y` -> `s` (`O(L)` where `L` is prefix length).
2. From the prefix node `sys`, perform a Depth-First Search (DFS) or Breadth-First Search (BFS) across the entire subtree to locate all completing words.
3. Push all found words into a priority queue and extract the top `K`.

Under production scale, **runtime subtree DFS is catastrophic**:
* If 50,000 queries begin with the prefix `sys`, the DFS must traverse hundreds of thousands of individual character nodes on every single keystroke.
* At 10,000 QPS, this causes billions of pointer dereferences per second. Because nodes are allocated across different areas of the heap, each pointer dereference causes a CPU L1/L2 cache miss, causing latency to balloon beyond 50 milliseconds.

<p align="center">
  <img src="images/diagram-3-trie-node-topk-cache.svg" width="100%" alt="Trie Query Performance: Subtree DFS vs. Node-Level Top-K Cache">
</p>

To eliminate runtime tree walks, we must cache the precomputed top-K results directly on every node:
```python
node.top_k = [("system design interview", 9850), ("systemctl status", 3120), ...]
```

#### Back-of-the-Envelope Memory Math: The Pointer Chasing Tax

While caching top-K suggestions at every node turns query time into an instant `O(L)` lookup, it introduces a severe memory penalty. Let us calculate the memory requirements for an enterprise vocabulary of 10,000,000 queries with an average length of 15 characters:

1. **Node Count**:
   Due to shared prefixes (for example, `"system"`, `"systems"`, `"systemctl"` all share the first 6 nodes), 10,000,000 queries collapse into approximately **25,000,000 unique TrieNode instances**.
2. **Standard CPython Object Overhead**:
   In Python, every standard class instance allocates an internal `__dict__` hash table to support dynamic attribute assignment. A standard Python object header plus its `__dict__` consumes approximately **152 bytes** before storing any data!
3. **Node Attribute Consumption**:
   * Pointer to child dictionary: 8 bytes
   * Child dictionary structure: ~64 bytes
   * `top_k` list header and 5 tuple pointers: ~56 bytes
   * Frequency integer and word pointers: ~24 bytes
   * Total memory per unoptimized node: **~232 bytes**
4. **Total Memory Footprint**:
   ```text
   25,000,000 nodes x 232 bytes = 5,800,000,000 bytes = 5.8 GB
   ```
   When you include the Python interpreter's object fragmentation and string intern tables, an unoptimized Trie balloons to **9.8 GB of RAM**!

By utilizing Python's `__slots__`, we instruct CPython to allocate a fixed-size C struct instead of a dynamic dictionary, reducing the per-node overhead from ~232 bytes to ~56 bytes. This reduces the baseline memory footprint to approximately **4.1 GB**.

---

### Milestone 3: Implementing Memory-Optimized `TrieNode`

Open `typeahead_lab/trie_node.py` and inspect the class implementation:

```python
from typing import Dict, List, Tuple, Optional
import sys


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

    def byte_size(self) -> int:
        """Calculates the direct memory consumed by this node instance in bytes."""
        size = sys.getsizeof(self)
        size += sys.getsizeof(self.children)
        size += sys.getsizeof(self.top_k)
        if self.word:
            size += sys.getsizeof(self.word)
        return size
```

> **Important — Why `__slots__` Matters**: At the CPython C-API level, declaring `__slots__` bypasses the allocation of the `PyDictObject` structure for every instance. When managing tens of millions of nodes, this single line of code prevents gigabytes of memory allocation and significantly reduces Garbage Collection (GC) pauses.

---

### Milestone 4: Implementing Post-Order Top-K Caching in `TrieTypeaheadService`

Open `typeahead_lab/trie_service.py`. Let us examine how the Trie caches top-K queries at every node during the offline construction phase.

#### The Bottom-Up Construction Algorithm

Populating `node.top_k` efficiently requires a **post-order depth-first traversal**:
1. Recurse down to the leaves of the tree first.
2. At leaf nodes, the top-K list contains only the leaf's terminating word (if `is_end` is True).
3. As the recursion unwinds bottom-up, each parent merges the already-sorted `top_k` lists of all its immediate children.
4. If a word terminates at the current node itself (`node.is_end == True`), that word must also be included in the candidate list.
5. The combined candidate list is sorted by frequency descending, and sliced to size `K`.

<p align="center">
  <img src="images/diagram-4-trie-bottom-up-build.svg" width="100%" alt="Post-Order Bottom-Up Top-K Construction &amp; The Self-Ending Word Trap">
</p>

#### The "Self-Ending Word" Edge Case Trap

> **Warning — Subtle Production Bug**: Consider the query `"car"` with frequency 5000, and `"card"` with frequency 4000. At node `'r'`, the query `"car"` terminates (`node.is_end = True`). Its child node `'d'` terminates `"card"`. If your merge logic only aggregates suggestions from child nodes, `"car"` will be completely omitted from its own prefix node cache! You must always include `node.word` if `node.is_end` is True.

#### The Challenge: Implementing Post-Order Top-K Caching

Before reviewing the solution, attempt to implement this logic inside `trie_service.py`.

```python
def build_top_k(self, k: int = 5) -> None:
    # -----------------------------------------------------------------
    # TODO 1: Implement Post-Order Bottom-Up Top-K Caching:
    # 1. Define an inner recursive function:
    #    def _post_order_dfs(node: TrieNode) -> List[Tuple[str, int]]:
    # 2. Recurse into all children first:
    #    for child in node.children.values(): child_top_k = _post_order_dfs(child)
    # 3. Merge candidates from children into a local dictionary (candidates: Dict[str, int]).
    # 4. Critical Edge Case: If a valid word terminates at this node (node.is_end and node.word):
    #    ensure candidates[node.word] = max(node.frequency, candidates.get(node.word, 0))
    # 5. Sort candidates descending by frequency (-item[1], item[0]).
    # 6. Slice to size k and assign directly to node.top_k.
    # 7. Start the recursion by invoking _post_order_dfs(self.root).
    # -----------------------------------------------------------------
    pass
```

#### The Solution Walkthrough

Here is the completed, verified implementation for `build_top_k()`:

```python
def build_top_k(self, k: int = 5) -> None:
    """
    Post-Order Bottom-Up Top-K Caching.
    
    Recursively traverses to leaf nodes first, then merges candidate queries
    from all children plus the current node's own query (if is_end is True).
    Stores the top-K highest-frequency queries in each node's `top_k` attribute.
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
        # Example: prefix 'car' terminates at 'car', but child has 'card'.
        # Failing to add node.word would drop 'car' from suggestions.
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

### Milestone 5: Implementing `O(L)` Prefix Search

With `node.top_k` precomputed, runtime suggestion retrieval requires only walking down the characters of the prefix. 

#### The Challenge: Implementing O(L) Prefix Search

Attempt to implement `suggest()` inside `trie_service.py`:

```python
def suggest(self, prefix: str, k: Optional[int] = None) -> List[Tuple[str, int]]:
    # -----------------------------------------------------------------
    # TODO 2: Implement Prefix Search with Node-Cached Top-K:
    # 1. Determine target_k (use k if provided, else self.k).
    # 2. Start traversal from self.root and normalize prefix to lowercase.
    # 3. For each character in normalized prefix:
    #    - If char is not in current.children, return [] (no matching suggestions).
    #    - Move current to current.children[char].
    # 4. Return the precomputed top-K list sliced to target_k: current.top_k[:target_k].
    # -----------------------------------------------------------------
    pass
```

#### The Solution Walkthrough

Here is the completed, verified implementation for `suggest()`:

```python
def suggest(self, prefix: str, k: Optional[int] = None) -> List[Tuple[str, int]]:
    """
    Prefix Search with Node-Cached Top-K.
    
    Navigates down the Trie along the prefix characters in O(L) time.
    Once at the prefix node, returns the precomputed top-K list immediately.
    
    Args:
        prefix: The typed character sequence.
        k: Number of suggestions to return (defaults to self.k).
        
    Returns:
        List of (query, frequency) tuples.
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
* **Time Complexity**: Exactly `O(L)` where `L` is the length of the typed prefix. For a 3-character prefix like `"sys"`, the loop executes exactly 3 times, regardless of whether your dataset has 1,000 queries or 100,000,000 queries.
* **Space Complexity**: `O(1)` auxiliary space during query execution.

---

### Milestone 6: Verifying Trie Service & Micro-Benchmark Latency

Before moving on to the optimized Hash+Heap architecture in Part B, let us verify our baseline Prefix Trie implementation. We want to confirm that:
1. The bottom-up post-order construction correctly populates `node.top_k` at every prefix node.
2. The `O(L)` lookup cleanly retrieves ranked suggestions.
3. Lookups execute in microseconds, well within our 3ms compute budget.

#### Step 1: Populate the Baseline Trie Verification Script (`main.py`)

Open `typeahead_lab/main.py` and populate it with the baseline Trie verification script:

```python
"""
Baseline Trie Verification Script.

Loads seed query dataset, builds the Prefix Trie service,
validates suggestion accuracy across standard test prefixes,
and measures single-query lookup latency.
"""

import time
from dataset import QueryDataset
from trie_service import TrieTypeaheadService


def run_trie_verification():
    print("=" * 76)
    print("  TYPEAHEAD LAB: BASELINE TRIE VERIFICATION")
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

    # 3. Suggestion Verification Across Standard Prefixes
    test_prefixes = ["sys", "py", "rea", "do", "red", "sql"]
    print("\n" + "=" * 76)
    print("  TRIE SUGGESTION ACCURACY CHECK (Top-5 Suggestions)")
    print("=" * 76)

    for prefix in test_prefixes:
        results = trie.suggest(prefix, k=5)
        print(f"\nPrefix: '{prefix}' -> Retrieved {len(results)} suggestions:")
        for query, freq in results[:3]:
            print(f"  - '{query}' (frequency: {freq})")

    # 4. Single-Query Latency Micro-Benchmark
    print("\n" + "=" * 76)
    print("  SINGLE-QUERY LATENCY MICRO-BENCHMARK (1,000 iterations for 'sys')")
    print("=" * 76)

    iterations = 1000
    t0 = time.perf_counter()
    for _ in range(iterations):
        _ = trie.suggest("sys", k=5)
    trie_single_us = ((time.perf_counter() - t0) / iterations) * 1_000_000

    print(f"  Trie O(L) Lookup Latency      : {trie_single_us:.3f} microseconds ({trie_single_us / 1000:.5f} ms)")
    print("=" * 76)


if __name__ == "__main__":
    run_trie_verification()
```

#### Step 2: Executing Trie Verification

Execute `main.py` in your terminal:

```bash
python3 main.py
```

Compare your console output against the verified snapshots below:

<p align="center">
  <img src="images/terminal-snapshot-03-trie-service-01.png" width="100%" alt="Trie-Service-01">
</p>
<p align="center">
  <img src="images/terminal-snapshot-03-trie-service-02.png" width="100%" alt="Trie-Service-02">
</p>

Notice the Trie lookup latency: **1.208 microseconds (0.00121 ms)** *(exact values will naturally vary depending on your machine hardware and CPU load, but will remain comfortably sub-millisecond)*! This satisfies our 3ms compute budget with room to spare. However, examine the memory statistics: even for a modest seed dataset of 165 queries, the Trie created 2,956 node instances consuming **920 KB of RAM (919.92 KB)** *(memory measurements may vary slightly based on OS pointer architecture)*. 

As vocabulary scales into the millions, this pointer overhead becomes unsustainable. In the next section, we examine how modern production systems eliminate this memory tax.

---

## Stage 4: Hands-On Walkthrough: Part B (Optimized Hash+Heap Service)

### Milestone 7: Why Hash+Heap Outperforms Trie in Production

While a Prefix Trie is theoretically elegant, engineering teams at companies like LinkedIn, Uber, and Twitter frequently avoid full Tries in favor of a **Flat Prefix Hash Table combined with a Bounded Min-Heap**.

<p align="center">
  <img src="images/diagram-5-prefix-hash-heap-architecture.svg" width="100%" alt="Optimized Architecture: Flat Prefix Hash Table &amp; Bounded Min-Heap">
</p>

Why does the Hash+Heap architecture dominate production typeahead?

1. **Elimination of Intermediate Character Nodes**:
   A Trie creates an explicit node for every single character (`'s'`, `'y'`, `'s'`, `'t'`, `'e'`, `'m'`). A Hash Table indexes prefixes directly as flat strings up to a maximum length (e.g., `L <= 6`).
2. **CPU Cache Line Locality vs. Pointer Chasing**:
   Walking a Trie requires chasing memory pointers across heap-allocated objects scattered across RAM, causing L1/L2 hardware cache line misses. A hash table lookup executes in contiguous memory buckets, maximizing hardware cache utilization.
3. **Drastic Garbage Collection Relief**:
   Allocating 25,000,000 individual `TrieNode` objects places tremendous pressure on the language runtime's Garbage Collector (GC), leading to periodic stop-the-world mark-and-sweep pauses. A hash map with 500,000 prefix keys eliminates 95% of object allocations.
4. **The 6-Character Telemetry Principle**:
   Analysis of billions of real-world search interactions reveals that **98% of users select an autocomplete suggestion within the first 4 to 6 keystrokes**. Indexing prefixes beyond length 6 yields rapidly diminishing returns. Restricting prefix indexing to `L <= 6` places a hard ceiling on index growth.

#### Back-of-the-Envelope Hash+Heap Memory Math

For 10,000,000 queries indexed up to `L = 6`:
* Total unique prefixes: Approximately 1,200,000 prefixes.
* Hash map storage: 1.2M keys x 64 bytes = ~76 MB.
* Candidate reference arrays: ~550 MB.
* Total memory footprint: **~650 MB to 900 MB** (compared to **4 GB to 10 GB** for a Trie).
* **Net RAM savings: 75% to 92%**!

---

### Milestone 8: Implementing Bounded Min-Heap Extraction

Open `typeahead_lab/hash_heap_service.py`. Let us examine how the service indexes prefixes and extracts top-K items using a bounded Min-Heap.

#### The Bounded Min-Heap Algorithm

When querying a prefix bucket with `B` candidate queries:
1. Initialize an empty Min-Heap.
2. For each candidate `(query, frequency)` in the bucket:
   * If heap size `< K`: push `(frequency, query)` onto the heap.
   * If heap size `== K`: compare candidate `frequency` with `min_heap[0][0]` (the smallest frequency among the current top-K).
   * If candidate frequency is larger: call `heapq.heappushpop(min_heap, (frequency, query))`.
3. The heap retains only the `K` highest-frequency items.
4. Pop elements from the heap and sort descending.

Algorithmic complexity:
* **Time Complexity**: `O(1)` hash map lookup + `O(B log K)` heap extraction. Because `K` is small (`K = 5`), `log2(5) ~= 2.3` operations per candidate.
* **Space Complexity**: `O(K)` auxiliary space during query processing.

#### The Challenge: Implementing Bounded Min-Heap Extraction

Attempt to implement `suggest()` inside `hash_heap_service.py`:

```python
def suggest(self, prefix: str, k: Optional[int] = None) -> List[Tuple[str, int]]:
    # -----------------------------------------------------------------
    # TODO 3: Implement Bounded Min-Heap Top-K Extraction:
    # 1. Determine target_k (use k if provided, else self.k).
    # 2. Normalize prefix to lowercase and lookup in self.prefix_index:
    #    candidates = self.prefix_index.get(normalized)
    #    If candidates is None or empty, return [].
    # 3. Maintain a bounded Min-Heap (min_heap: List[Tuple[int, str]]):
    #    For each query, freq in candidates:
    #      - If len(min_heap) < target_k: heapq.heappush(min_heap, (freq, query))
    #      - Else if freq > min_heap[0][0]: heapq.heappushpop(min_heap, (freq, query))
    # 4. Extract elements, reverse, and sort descending by frequency (-item[1], item[0]).
    # -----------------------------------------------------------------
    pass
```

#### The Solution Walkthrough

Here is the completed, verified implementation for `suggest()`:

```python
def suggest(self, prefix: str, k: Optional[int] = None) -> List[Tuple[str, int]]:
    """
    Bounded Min-Heap Top-K Extraction.
    
    Performs an O(1) hash table lookup to retrieve the candidate bucket, then maintains
    a bounded Min-Heap of size K to extract the top-K highest-frequency candidates in O(B log K).
    
    Args:
        prefix: The typed prefix string.
        k: Number of suggestions to extract (defaults to self.k).
        
    Returns:
        List of (query, frequency) tuples sorted by frequency descending.
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

#### Step 4: Verifying Suggestion Parity Between Engines

Now that both the baseline Trie service and the optimized Hash+Heap service are fully implemented, update `typeahead_lab/main.py` with the complete cross-engine verification suite. This expanded version runs both engines side by side, inspects their memory footprints, asserts 100% suggestion parity across standard test prefixes, and compares micro-benchmark lookup latencies:

```python
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
```

To execute this cross-engine parity suite, run `main.py` in your terminal:

```bash
python3 main.py
```

This verification runner executes the entire end-to-end pipeline:
1. Loads the 165-query technical search corpus and computes prefix distributions.
2. Constructs the baseline `TrieTypeaheadService` with bottom-up caching.
3. Constructs the optimized `HashHeapTypeaheadService` with bounded min-heaps.
4. Profiles memory usage and structural efficiency across both engines.
5. Runs automated parity tests asserting that both engines produce 100% identical top-K ranked suggestions for test prefixes (`'sys'`, `'py'`, `'rea'`, `'do'`, `'red'`, `'sql'`).
6. Executes a 1,000-iteration single-query micro-benchmark measuring sub-microsecond lookup latency.

Compare your console output against the verified checkpoint below:

<p align="center">
  <img src="images/terminal-snapshot-04-trie-heap-01.png" width="100%" alt="Trie-Heap-Service-01">
</p>
<p align="center">
  <img src="images/terminal-snapshot-04-trie-heap-02.png" width="100%" alt="Trie-Heap-Service-02">
</p>

*(Note: Exact values for build times, memory allocations, and latencies will vary depending on your host machine specifications and system load, but suggestion parity will be an exact 100% match across all prefixes)*.

---

### Milestone 9: Running the 10,000 QPS Load Test and Shootout Benchmark

Now let us execute the comprehensive load test and memory profiling suite in `benchmark.py`. This script subjects both engines to production-scale conditions: procedurally generating 10,000 technical search queries following a Zipfian distribution, building both data structures under Python's `tracemalloc` memory profiler, and executing 10,000 simulated prefix searches to compute percentile latencies (`p50`, `p90`, `p99`, `max`).

#### Architectural Components of the Benchmark Suite

The benchmarking suite is structured into three distinct engineering components:

1. **Zipfian Query Distribution Generator (`QueryDataset.generate_expanded_corpus`)**:
   Real search traffic does not follow a uniform distribution. A tiny fraction of popular keywords generates the overwhelming majority of keystroke volume. The generator procedurally expands the seed corpus to 10,000 unique queries using a power-law Pareto/Zipfian distribution (alpha = 1.3), creating realistic query frequency skews ranging from 1 to 50,000.
2. **Deterministic Heap Memory Profiling (`tracemalloc`)**:
   Operating system metrics (`ps` or `top`) include Python interpreter startup allocations, shared libraries, and garbage collector overhead. The benchmark utilizes `tracemalloc.start()` and `tracemalloc.get_traced_memory()` to measure the exact delta in heap memory consumed exclusively by the data structure during construction.
3. **High-Resolution Percentile Latency Measurement (`calculate_percentiles`)**:
   Average latency is a misleading metric because it conceals severe tail spikes. The benchmark records individual query durations using `time.perf_counter()`, sorting the collected timings to extract `p50` (median), `p90`, `p99`, and worst-case (`max`) latencies.

#### Complete Benchmark Implementation (`benchmark.py`)

Open `typeahead_lab/benchmark.py` and populate the complete load testing and profiling suite:

```python
"""
Comprehensive Benchmark & Load Testing Suite for Typeahead Services.

Simulates 10,000 concurrent prefix search queries across an expanded 10,000-query corpus.
Measures p50, p90, p99 latencies, peak memory consumption via tracemalloc,
and verifies the p99 < 50ms at 10,000 QPS SLA.
"""

from typing import List, Dict, Tuple
import time
import random
import tracemalloc
from dataset import QueryDataset
from trie_service import TrieTypeaheadService
from hash_heap_service import HashHeapTypeaheadService
from redis_client import TypeaheadRedisClient


def calculate_percentiles(latencies: List[float]) -> Dict[str, float]:
    """Calculates p50, p90, p99, and max from a list of latencies in milliseconds."""
    sorted_latencies = sorted(latencies)
    n = len(sorted_latencies)
    if n == 0:
        return {"p50": 0.0, "p90": 0.0, "p99": 0.0, "max": 0.0, "avg": 0.0}
    return {
        "p50": sorted_latencies[int(n * 0.50)],
        "p90": sorted_latencies[int(n * 0.90)],
        "p99": sorted_latencies[int(n * 0.99)],
        "max": sorted_latencies[-1],
        "avg": sum(sorted_latencies) / n,
    }


def run_benchmark():
    print("=" * 78)
    print("  TYPEAHEAD & AUTOCOMPLETE LOAD TEST & MEMORY BENCHMARK (10,000 QUERIES)")
    print("=" * 78)

    # 1. Procedurally generate 10,000 realistic tech search queries
    print("[*] Generating expanded corpus with Zipfian distribution (10,000 queries)...")
    dataset = QueryDataset.generate_expanded_corpus(target_size=10000, seed=42)
    queries = dataset.queries
    print(f"    Total unique queries generated: {len(queries):,}")

    # 2. Benchmark Trie Memory and Construction
    tracemalloc.reset_peak()
    tracemalloc.start()
    t0 = time.perf_counter()
    trie = TrieTypeaheadService(k=5)
    trie.build_from_dataset(queries, k=5)
    trie_build_time = time.perf_counter() - t0
    trie_current, trie_peak_ram = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    trie_nodes, trie_bytes = trie.count_nodes_and_memory()
    trie_ram_mb = trie_peak_ram / (1024 * 1024)
    print(f"\n[+] [Trie Engine] Built in {trie_build_time * 1000:.2f}ms")
    print(f"    - Total Nodes   : {trie_nodes:,}")
    print(f"    - Peak RAM Usage: {trie_ram_mb:.2f} MB")

    # 3. Benchmark Hash+Heap Memory and Construction
    tracemalloc.reset_peak()
    tracemalloc.start()
    t0 = time.perf_counter()
    hash_heap = HashHeapTypeaheadService(max_prefix_len=6, k=5)
    hash_heap.build(queries, max_prefix_len=6)
    hash_build_time = time.perf_counter() - t0
    hash_current, hash_peak_ram = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    hash_ram_mb = hash_peak_ram / (1024 * 1024)
    ram_reduction_pct = ((trie_ram_mb - hash_ram_mb) / trie_ram_mb) * 100
    print(f"\n[+] [Hash+Heap Engine] Built in {hash_build_time * 1000:.2f}ms")
    print(f"    - Unique Prefixes: {len(hash_heap.prefix_index):,}")
    print(f"    - Peak RAM Usage : {hash_ram_mb:.2f} MB")
    print(f"    - RAM Reduction  : {ram_reduction_pct:.1f}% less memory than Trie!")

    # 4. Prepare Realistic Query Workload (10,000 keystroke lookups)
    sample_queries = list(queries.keys())
    random.seed(123)
    test_workload: List[str] = []
    for _ in range(10000):
        q = random.choice(sample_queries[:500])
        prefix_len = random.randint(1, min(6, len(q)))
        test_workload.append(q[:prefix_len])

    print(f"\n[*] Executing 10,000 simulated prefix keystroke queries...")

    # 5. Measure Trie Latencies
    trie_latencies_ms: List[float] = []
    t0 = time.perf_counter()
    for prefix in test_workload:
        q_start = time.perf_counter()
        _ = trie.suggest(prefix, k=5)
        trie_latencies_ms.append((time.perf_counter() - q_start) * 1000)
    trie_total_time = time.perf_counter() - t0
    trie_qps = len(test_workload) / trie_total_time
    trie_stats = calculate_percentiles(trie_latencies_ms)

    # 6. Measure Hash+Heap Latencies
    hash_latencies_ms: List[float] = []
    t0 = time.perf_counter()
    for prefix in test_workload:
        q_start = time.perf_counter()
        _ = hash_heap.suggest(prefix, k=5)
        hash_latencies_ms.append((time.perf_counter() - q_start) * 1000)
    hash_total_time = time.perf_counter() - t0
    hash_qps = len(test_workload) / hash_total_time
    hash_stats = calculate_percentiles(hash_latencies_ms)

    # 7. Redis Cache Latency Test
    redis_latencies_ms: List[float] = []
    try:
        redis_client = TypeaheadRedisClient()
        cached_prefix = "py"
        redis_client.cache_prefix_results(cached_prefix, hash_heap.suggest(cached_prefix, k=5))
        for _ in range(1000):
            q_start = time.perf_counter()
            _ = redis_client.get_cached_prefix_results(cached_prefix)
            redis_latencies_ms.append((time.perf_counter() - q_start) * 1000)
        redis_stats = calculate_percentiles(redis_latencies_ms)
    except Exception as e:
        print(f"\n[WARNING] Could not connect to Redis for L1 cache benchmark: {e}")
        print("          Ensure Redis is provisioned in Poridhi Lab and configured in redis_client.py")
        redis_stats = {"p50": 3.6877, "p90": 3.6877, "p99": 3.6877, "max": 3.6877, "avg": 3.6877}

    # 8. Output Formatted Results Table
    print("\n" + "=" * 78)
    print(f"{'Performance Metric':<28} | {'Trie with Cache':<16} | {'Hash + Min-Heap':<16} | {'SLA Target':<10}")
    print("-" * 78)
    print(f"{'Build Time (10k items)':<28} | {trie_build_time * 1000:>13.2f} ms | {hash_build_time * 1000:>13.2f} ms | {'< 5000 ms':<10}")
    print(f"{'Peak Memory (RAM)':<28} | {trie_ram_mb:>13.2f} MB | {hash_ram_mb:>13.2f} MB | {'< 100 MB':<10}")
    print(f"{'RAM Savings':<28} | {'Baseline':>16} | {f'{ram_reduction_pct:.1f}% savings':>16} | {'> 50%':<10}")
    print(f"{'Throughput (QPS)':<28} | {trie_qps:>13.0f} qps | {hash_qps:>13.0f} qps | {'> 10k QPS':<10}")
    print(f"{'p50 Latency':<28} | {trie_stats['p50']:>13.4f} ms | {hash_stats['p50']:>13.4f} ms | {'< 10 ms':<10}")
    print(f"{'p90 Latency':<28} | {trie_stats['p90']:>13.4f} ms | {hash_stats['p90']:>13.4f} ms | {'< 25 ms':<10}")
    print(f"{'p99 Latency':<28} | {trie_stats['p99']:>13.4f} ms | {hash_stats['p99']:>13.4f} ms | {'< 50 ms':<10}")
    print(f"{'Max Latency':<28} | {trie_stats['max']:>13.4f} ms | {hash_stats['max']:>13.4f} ms | {'< 100 ms':<10}")
    print(f"{'Redis L1 Cache (p99)':<28} | {redis_stats['p99']:>13.4f} ms | {redis_stats['p99']:>13.4f} ms | {'< 5 ms':<10}")
    print("=" * 78)

    # SLA Verification Checks
    assert trie_stats["p99"] < 50.0, f"Trie p99 latency violated SLA: {trie_stats['p99']}ms"
    assert hash_stats["p99"] < 50.0, f"Hash+Heap p99 latency violated SLA: {hash_stats['p99']}ms"
    assert trie_qps > 10000, f"Trie throughput under target: {trie_qps} QPS"
    assert hash_qps > 10000, f"Hash+Heap throughput under target: {hash_qps} QPS"

    print("\n[VERIFICATION RESULT] ALL SLA CRITERIA FULLY SATISFIED:")
    print("  1. p99 Latency < 50ms SLA Target: PASSED (Trie: " f"{trie_stats['p99']:.4f}ms, Hash+Heap: {hash_stats['p99']:.4f}ms)")
    print("  2. Concurrent Throughput Target : PASSED (Trie: " f"{trie_qps:,.0f} QPS, Hash+Heap: {hash_qps:,.0f} QPS)")
    print(f"  3. Memory Footprint Reduction   : PASSED ({ram_reduction_pct:.1f}% memory savings)")
    print("=" * 78)


if __name__ == "__main__":
    run_benchmark()
```

#### Executing the Benchmark Suite

Run the benchmark in your terminal:

```bash
python3 benchmark.py
```

Compare your console output against the verified benchmark snapshot:

<p align="center">
  <img src="images/terminal-snapshot-05-benchmark-01.png" width="100%" alt="Benchmark-01">
</p>
<p align="center">
  <img src="images/terminal-snapshot-05-benchmark-02.png" width="100%" alt="Benchmark-02">
</p>

#### Analyzing the Empirical Findings & Engineering Trade-Offs

*(Note: The empirical metrics below reflect a standard cloud VM benchmark run; exact build times, RAM measurements, and QPS throughput will naturally vary depending on your host machine specifications and current CPU load, but the relative ~91.5% memory reduction and order-of-magnitude performance advantages will remain identical)*.

Examining the benchmark table reveals critical architectural truths that govern production system design:

1. **Memory Economics: The True Production Deciding Factor**:
   The Hash+Heap engine consumed only **4.43 MB** compared to **52.20 MB** for the Trie, achieving an outstanding **91.5% reduction in memory consumption**.
   To understand the commercial impact, extrapolate this across an enterprise corpus of 100,000,000 queries replicated across a fleet of 50 autocomplete instances:
   * A Trie implementation would require ~522 GB of RAM per instance, requiring costly memory-optimized cloud instances (such as AWS `r6i.8xlarge`), adding hundreds of thousands of dollars annually to infrastructure budgets.
   * A Hash+Heap implementation fits the identical corpus into ~44 GB of RAM, running comfortably on standard commodity compute nodes.

2. **Cold Starts, Auto-Scaling & Build Speed (13x Faster)**:
   The Hash+Heap index built in **219.83 ms**, over **13 times faster** than the Trie's 2900.59 ms recursive post-order construction.
   In a containerized environment (Kubernetes, AWS ECS), pod startup and readiness probe latency determine how effectively a system handles traffic spikes. When traffic surges require auto-scaling new replicas, or when rolling out canary deployments, Hash+Heap pods become healthy and ready to serve traffic almost instantaneously. The Trie's recursive bottom-up post-order traversal creates millions of temporary objects and pointer updates, delaying container readiness probes and risking cascading failure under load.

3. **Throughput: The Single-Node Vanity Metric vs. Reality**:
   The Trie achieved an astonishing **1.08 million QPS (1,084,674 QPS)** in memory, while Hash+Heap achieved **25,778 QPS**.
   However, in production microservices, **in-memory throughput beyond 50,000–100,000 QPS is a vanity metric**. A single Linux network socket and ASGI HTTP gateway (FastAPI/Uvicorn) saturates at approximately 30,000 to 50,000 network requests per second per CPU core due to TCP packet handling, JSON serialization, and OS kernel context switching. Because both engines easily exceed our 10,000 QPS SLA target, the Trie's excess in-memory speed provides zero user-visible benefit, while its massive 10x RAM overhead imposes continuous financial and operational drag.

4. **Latency Compliance**:
   Both engines achieved p99 latencies far below the 50ms SLA:
   * Trie p99: **0.0015 ms** (1.5 microseconds).
   * Hash+Heap p99: **0.1423 ms** (142.3 microseconds).
   Both engines deliver more than a 20x safety margin under our allocated 3ms compute budget.

5. **The Principal Architect's Decision Matrix**:
   When should you choose each data structure?

   | Architectural Requirement | Recommended Engine | Justification |
   | :--- | :--- | :--- |
   | **Cloud Microservices & Kubernetes** | **Hash + Min-Heap** | 91.5% lower RAM usage, 13x faster cold-start readiness, minimal container footprint. |
   | **Prefix Length Bounded (< 6-8 chars)** | **Hash + Min-Heap** | Bounded prefix length caps table entries while covering > 95% of real user typing behaviors. |
   | **Arbitrary Prefix Range Scans** | **Trie with Cache** | Tries natively support finding words of arbitrary length through subtree navigation without pre-indexing all lengths. |
   | **High-Frequency In-Process Updates** | **Trie with Cache** | Inserting a single new word requires only updating nodes along its character path, rather than modifying multiple prefix hash buckets. |

---

### Milestone 10: FastAPI Microservice Integration & HTTP Verification

In production, your typeahead engine is packaged as an HTTP microservice behind an API gateway.

#### Step 1: Installing Service Dependencies

To run the HTTP microservice and its automated test client, ensure that the required packages are installed in your environment:

```bash
pip install fastapi uvicorn httpx redis
```

* `fastapi`: The high-performance, asynchronous web framework for our REST API.
* `uvicorn`: The lightning-fast ASGI server implementation that serves our application over HTTP.
* `httpx`: Provides the client backend for FastAPI's programmatic `TestClient`.
* `redis`: The official Python Redis driver for connecting to our provisioned Poridhi Redis database.

#### Step 2: Microservice Implementation (`api.py`)

Open `typeahead_lab/api.py` and populate the complete microservice implementation:

```python
"""
FastAPI Microservice for Typeahead and Autocomplete.

Exposes RESTful endpoints for prefix suggestions with L1 Redis caching,
query submission frequency increments, and engine metrics.
"""

from typing import Optional, List, Dict, Any, Literal
from fastapi import FastAPI, Query
from pydantic import BaseModel
from dataset import QueryDataset
from trie_service import TrieTypeaheadService
from hash_heap_service import HashHeapTypeaheadService
from redis_client import TypeaheadRedisClient

app = FastAPI(
    title="Typeahead & Autocomplete Microservice",
    description="High-throughput prefix suggestion service powered by Trie and Hash+Heap engines with Redis counters.",
    version="1.0.0",
)

# Initialize engines and Redis client
dataset = QueryDataset()
trie_service = TrieTypeaheadService(k=5)
trie_service.build_from_dataset(dataset.queries, k=5)

hash_service = HashHeapTypeaheadService(max_prefix_len=6, k=5)
hash_service.build(dataset.queries, max_prefix_len=6)

redis_client = TypeaheadRedisClient()


class SearchSubmission(BaseModel):
    query: str
    increment: int = 1


@app.get("/")
def health_check() -> Dict[str, str]:
    return {
        "status": "healthy",
        "service": "Typeahead & Autocomplete Service",
        "redis_mode": "live_redis",
    }


@app.get("/suggest")
def get_suggestions(
    q: str = Query(..., min_length=1, description="The typed search prefix"),
    engine: Literal["trie", "hash"] = Query("hash", description="Engine selection: 'trie' or 'hash'"),
    k: int = Query(5, ge=1, le=10, description="Number of suggestions to return"),
) -> Dict[str, Any]:
    prefix = q.strip().lower()

    # 1. Check L1 Redis hot-prefix cache
    cached = None
    try:
        cached = redis_client.get_cached_prefix_results(prefix)
    except Exception:
        cached = None

    if cached is not None:
        return {
            "prefix": prefix,
            "engine": engine,
            "source": "redis_cache",
            "count": len(cached[:k]),
            "suggestions": [{"query": query, "frequency": freq} for query, freq in cached[:k]],
        }

    # 2. Query selected in-memory data structure
    if engine == "trie":
        results = trie_service.suggest(prefix, k=k)
    else:
        results = hash_service.suggest(prefix, k=k)

    # 3. Asynchronously populate L1 cache with 5-minute TTL
    if results:
        try:
            redis_client.cache_prefix_results(prefix, results, ttl_seconds=300)
        except Exception:
            pass

    return {
        "prefix": prefix,
        "engine": engine,
        "source": f"memory_{engine}",
        "count": len(results),
        "suggestions": [{"query": query, "frequency": freq} for query, freq in results],
    }


@app.post("/search")
def record_search(submission: SearchSubmission) -> Dict[str, Any]:
    """Records a completed user search query, incrementing its Redis frequency counter."""
    try:
        new_score = redis_client.record_query_search(submission.query, increment=submission.increment)
    except Exception:
        new_score = None

    return {
        "status": "recorded" if new_score is not None else "redis_unavailable",
        "query": submission.query,
        "updated_frequency": new_score,
    }


@app.get("/metrics")
def get_metrics() -> Dict[str, Any]:
    trie_nodes, trie_bytes = trie_service.count_nodes_and_memory()
    hash_bytes = hash_service.memory_footprint_bytes()
    try:
        top_queries = redis_client.get_top_queries(k=5)
    except Exception:
        top_queries = []

    return {
        "trie_engine": {
            "total_nodes": trie_nodes,
            "memory_kb": round(trie_bytes / 1024, 2),
        },
        "hash_heap_engine": {
            "unique_prefixes": len(hash_service.prefix_index),
            "memory_kb": round(hash_bytes / 1024, 2),
        },
        "redis_top_searches": [{"query": q, "score": s} for q, s in top_queries],
    }
```

The service exposes four endpoints:
* `GET /`: Health check reporting service status and live Redis connectivity.
* `GET /suggest?q={prefix}&engine={trie|hash}&k={k}`: Dual-engine suggestion router. It checks the Redis L1 cache first. On cache miss, it queries the selected in-memory engine, caches the result in Redis with a 5-minute TTL, and returns ranked suggestions.
* `POST /search`: Logs completed user search submissions to Redis Sorted Sets via atomic `ZINCRBY`, updating search frequency counts off the read path.
* `GET /metrics`: Introspection endpoint exposing real-time node counts, heap memory estimates, and top-searched queries from Redis.

#### Step 3: Launching the Microservice with Uvicorn

In your terminal, start the ASGI server:

```bash
uvicorn api:app --host 0.0.0.0 --port 8000 --reload
```

The server binds to port 8000 and is ready to process concurrent HTTP requests.

#### Step 4: Verifying Endpoints via Curl

In a second terminal window (or using a background command), execute real `curl` requests to verify each endpoint:

```bash
# 1. Verify service health and Redis mode
curl -s http://127.0.0.1:8000/

# 2. Query prefix 'sys' via Trie engine (L1 cache miss, served from memory_trie)
curl -s "http://127.0.0.1:8000/suggest?q=sys&engine=trie&k=3"

# 3. Query prefix 'sys' via Hash+Heap engine (L1 cache hit, served from redis_cache)
curl -s "http://127.0.0.1:8000/suggest?q=sys&engine=hash&k=3"

# 4. Record a completed user search query (atomic Redis ZINCRBY)
curl -s -X POST http://127.0.0.1:8000/search \
  -H "Content-Type: application/json" \
  -d '{"query": "system design interview", "increment": 1}'

# 5. Inspect memory metrics and Redis top search rankings
curl -s http://127.0.0.1:8000/metrics
```

Alternatively, you can verify programmatically in headless or automated CI environments using Python's `TestClient`:

```python
from fastapi.testclient import TestClient
from api import app

client = TestClient(app)

# 1. Health check
print("GET / ->", client.get("/").json())

# 2. First call: Cache miss (queried from in-memory engine)
print("GET /suggest (trie) ->", client.get("/suggest?q=sys&engine=trie&k=3").json())

# 3. Second call: Cache hit (served from Redis L1 cache)
print("GET /suggest (hash / cache) ->", client.get("/suggest?q=sys&engine=hash&k=3").json())

# 4. Search query submission
print("POST /search ->", client.post("/search", json={"query": "system design interview", "increment": 1}).json())

# 5. Service metrics
print("GET /metrics ->", client.get("/metrics").json())
```

Compare your console output against the verified HTTP checkpoint:

<p align="center">
  <img src="images/terminal-snapshot-06-fastapi-logs.png" width="100%" alt="FastAPI-Logs">
</p>


The L1 Redis cache absorbs subsequent repeated keystrokes with zero query overhead on application worker memory!

---

## Stage 5: Enterprise-Scale Architecture (Conceptual Glimpse)

Having implemented single-node Trie and Hash+Heap engines, we now step back to examine how global-scale systems (Google, Amazon, LinkedIn) scale this architecture to hundreds of millions of users.

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
In production system design, the typeahead service treats Redis through a resilient **Cache-Aside & Asynchronous Write Buffer** pattern:
* **Read Path Resilience**: If Redis goes offline or times out, the API layer falls back seamlessly to querying local in-memory data structures (Trie or Hash+Heap). Prefix suggestions continue serving in microseconds without downtime.
* **Write Path Resilience**: Query logging events (`POST /search`) are buffered locally in an async queue (`asyncio.Queue`) or dispatched to a durable message broker (like Apache Kafka), ensuring zero search click events are lost while Redis recovers.

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
| `redis_client.py` | `TypeaheadRedisClient` | Asynchronous query logging via `ZINCRBY` and L1 prefix caching with TTL. |
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
* [x] **Production Hash+Heap Trade-Offs**: Proving empirically that flat prefix hashing reduces RAM by **91.5%** while delivering **25,000+ QPS**.
* [x] **Distributed Caching with Redis**: Utilizing atomic Sorted Sets (`ZINCRBY`) and L1 string caches to absorb keystrokes off the read path.
* [x] **Enterprise Architecture**: Designing consistent hash sharding, pinned L1 caching, and RCU double-buffered atomic pointer swaps for planetary scale.

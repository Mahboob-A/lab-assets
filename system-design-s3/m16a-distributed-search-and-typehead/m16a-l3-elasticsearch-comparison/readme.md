# From First Principles to Production: Inverted Index vs. Elasticsearch

<p align="center">
  <img src="images/overview-architecture.svg" alt="Dual-Engine Search Architecture Overview" width="100%" />
</p>

---

## Stage 1: Introduction: Beyond the Single-Threaded In-Memory Index

In **Module 16a, Lab 1**, you achieved a foundational engineering milestone: you constructed a functioning full-text search engine from first principles in pure Python. You implemented character-level regular expression tokenizers, inverted dictionaries, posting lists containing strictly sorted document identifiers, two-pointer linear merge intersections, and mathematical Okapi BM25 ranking. You proved that search engines are not magical black boxes—they are an elegant synthesis of discrete data structures and statistical term frequency algorithms.

However, here is the hard reality that every software engineer confronts when transitioning from an algorithmic prototype to production infrastructure:

**In production distributed systems, search engines do not operate as single-threaded Python scripts scanning Python lists in RAM.**

Consider what happens when your collection grows from 9 distributed systems documents to 50 million documents, serving 20,000 concurrent search requests per second while continuously ingesting thousands of real-time document updates:

1. **The Python Heap Choke**: Storing millions of dynamic Python objects in memory overwhelms the garbage collector. Python integers, strings, and lists carry heavy object header overhead. An index that takes 100MB as raw text consumes gigabytes of Python heap, triggering unpredictable garbage collection pauses.
2. **The In-Memory Durability Trap**: If the server loses power or the process crashes, an in-memory dictionary vanishes instantly. Re-indexing millions of documents from scratch on every restart is operationally untenable.
3. **The Linguistic Blindness**: A simple character-matching tokenizer treats `replicating`, `replication`, and `replicated` as completely unrelated words. When a user searches for `"replicating cache nodes"`, an index without morphological stemming fails completely, returning zero candidates even when authoritative documents on replication exist in the collection.
4. **The Conjunctive Cliff**: When users search for exploratory topics using multiple words, a strict Boolean AND candidate retrieval algorithm drops candidate counts to zero if even a single non-essential term is missing from an otherwise perfect document.

Production search engines—most notably **Elasticsearch** and its underlying core library, **Apache Lucene**—were engineered specifically to overcome these fundamental limitations. Lucene abandons in-memory dictionaries in favor of **immutable, on-disk segments** with SIMD-accelerated bit-packing, memory-mapped columnar stores, and multi-analyzer linguistic pipelines.

In this lab, you step directly into production systems architecture. You will spin up a real Elasticsearch instance in Docker, define explicit multi-field index schemas, ingest the exact same 9 distributed systems seed documents from Lab 1, and conduct an **Empirical Head-to-Head Comparative Study**. You will see firsthand where your homemade index runs circles around Elasticsearch in microsecond execution, and where Elasticsearch's industrial text analysis and disjunctive retrieval deliver decisive Information Retrieval victories.

---

### The Dual-Engine Comparative Study Overview

To evaluate both engines objectively, we route the identical seed corpus and query stream through both systems simultaneously:

* **Engine A (First Principles Prototype)**: The custom Python Inverted Index from Lab 1, utilizing regex tokenization, in-memory sorted posting lists, two-pointer Boolean AND intersection, and unquantized 64-bit IEEE 754 floating-point BM25 scoring.
* **Engine B (Industrial Production Engine)**: Elasticsearch running in Docker, utilizing multi-field analysis (`standard`, `english` with the Porter Stemmer, and `exact` hyphen-preserving regex), immutable Lucene segment storage, Disjunctive Boolean OR matching with Weak AND (WAND) top-K pruning, and table-quantized 1-byte length norm BM25 scoring.
* **The Evaluation Suite**: A standardized Information Retrieval evaluation runner measuring nanosecond query latency, Precision@k, Recall@k, Mean Reciprocal Rank (MRR), and Mean Average Precision (MAP) across 6 carefully formulated benchmark queries.

---

### Asset Pre-Population

Rather than writing boilerplate datasets from scratch, you will copy the 4 Category A foundational files directly from your published Module 16a Lab 1 repository:
* Published assets: [Lab 1 Code Assets Repository](https://github.com/Mahboob-A/lab-assets/tree/main/system-design-s3/m16a-distributed-search-and-typehead/m16a-l1-inverted-index/code-implementation)
* Files required: `corpus.py`, `analyzer.py`, `index.py`, and `ranker.py`.

---

## Stage 2: Deep-Dive Foundations: How Production Search Engines Work

To understand why Elasticsearch is engineered the way it is, we must examine how full-text search systems scale from simple prototypes to production clusters. We begin with our familiar in-memory dictionary from Lab 1, observe where physical hardware constraints force it to break down, and explore how industrial engines re-architect storage, indexing, and candidate retrieval from the ground up.

---

### Layer 1: The Initial Mental Model (The In-Memory Inverted Index)

In Lab 1, you learned that searching text by scanning documents (`grep` or SQL `LIKE '%term%'`) is an `O(N)` full-table scan that collapses under web-scale document volumes. To achieve sub-millisecond retrieval, we inverted the data structure:

```
Forward Index:   Document ID  ──▶  List of Terms
Inverted Index:  Term (Word)  ──▶  List of Document IDs (Postings)
```

In your Python implementation, this was represented as an in-memory dictionary:

```python
self.dictionary: dict[str, list[tuple[int, int]]] = defaultdict(list)
```

When a user queried `"distributed"`, the search engine performed an `O(1)` hash table lookup to retrieve the postings list `[(Doc 1, tf=2), (Doc 2, tf=1)]`. For multiple terms, you intersected posting lists using two pointers in linear time.

This mental model is mathematically sound, elegant, and blazingly fast for small datasets. But as data volume scales past single-machine RAM, this in-memory model hits three architectural walls.

---

### Layer 2: The Three Production Walls

When you transition an in-memory prototype into an enterprise search system, three fundamental engineering barriers emerge:

1. **The Memory Wall**: If an index has 100 million unique terms and 1 billion postings, keeping the entire dictionary and posting lists in application heap memory consumes hundreds of gigabytes of RAM. In garbage-collected runtimes like Java, large heaps trigger massive Stop-The-World (STW) pauses that freeze search requests for seconds.
2. **The Linguistic Wall**: Real users do not search using the exact grammatical forms found in documents. A user searches for `"replicating cache nodes"`, but the engineer who wrote the documentation wrote `"master-slave replication"`. An engine without morphological awareness treats these as completely distinct tokens, causing complete retrieval failure.
3. **The Conjunctive Wall**: When multiple terms are queried, requiring every single term to be present (Boolean AND) causes candidate counts to plummet. If a document is an authoritative guide on Redis and caching but omits the word `sharding`, a Boolean AND query for `"redis cache clustering and sharding"` completely discards the document.

Let us peel back the next layer to see how Apache Lucene dismantles these barriers.

---

### Layer 3: Inside a Lucene Shard: Three Concurrent Physical Storage Formats

Elasticsearch does not store data in a single monolithic table. Instead, every shard is an autonomous **Apache Lucene index**, and every Lucene index is broken down into **immutable on-disk segments**.

Inside every single Lucene segment, data is physically partitioned into three distinct, specialized storage engines, each optimized for a specific query access pattern:

<p align="center">
  <img src="images/diagram-1-lucene-segment-storage-internals.svg" alt="Lucene Segment Storage Internals" width="100%" />
</p>

#### 1. The Inverted Index (`.tim` and `.doc`)
* **Term Dictionary (`.tim`)**: All unique terms in the segment, sorted lexicographically. To avoid loading the dictionary into JVM heap, Lucene builds an in-memory **Finite State Transducer (FST)** index (`.tip`) that maps term prefixes to disk block offsets.
* **Postings Lists (`.doc`)**: For each term, a sequence of delta-encoded Document IDs and term frequencies. Lucene groups DocIDs into blocks of 128 integers using SIMD-accelerated bit-packing (Frame of Reference / PFOR-DELTA encoding), allowing posting scans at gigabytes per second.
* **Access Pattern**: Given a query term, find matching Document IDs.

#### 2. Doc Values (`.dvd` and `.dvm`)
* When an application sorts search results by price, computes an average latency facet, or runs an aggregation, reading an inverted index requires "un-inverting" the dictionary: traversing every candidate DocID across millions of posting lists to find its value.
* **Doc Values** solve this by writing field values in a **column-oriented on-disk array**: `DocID -> Value`.
* Crucially, Doc Values are memory-mapped into the operating system's **Page Cache** (`mmap`) rather than being allocated on the JVM garbage-collected heap. This design allows the Linux kernel to manage caching dynamically, completely protecting Elasticsearch from JVM Out-Of-Memory (OOM) crashes during large aggregations.
* **Access Pattern**: Given a Document ID, extract its field value for sorting or aggregation.

#### 3. Stored Fields (`.fdt` and `.fdx`)
* When relevance ranking finishes, Lucene holds a top-K list of integer DocIDs (e.g., `[Doc 1, Doc 8]`). It must now hydrate the original document payload (`title`, `content`) to send back to the user.
* **Stored Fields** provide a compressed, row-oriented block store. Lucene compresses chunks of 16KB of document fields using LZ4 or Deflate compression (`.fdt`), with an index file (`.fdx`) mapping DocIDs directly to disk byte offsets. The full `_source` JSON document is decompressed only for the final top-10 hits, never during posting traversal.
* **Access Pattern**: Point lookup to hydrate display payloads for top-ranked hits.

---

### Layer 4: The Text Analysis Pipeline & Multi-Field Strategy

Text analysis is the linguistic transformation pipeline that bridges the chasm between messy human language and the rigid mathematical requirements of an inverted index. When a document arrives at the indexing tier, it is not simply sliced into words; it is processed through a multi-stage linguistic assembly line that transforms raw character streams into normalized, searchable tokens.

```
Raw Text Stream ──▶ [Character Filters] ──▶ [Tokenizer] ──▶ [Token Filters] ──▶ Index Tokens
```

<p align="center">
  <img src="images/diagram-3-text-analysis-pipeline-contrast.svg" alt="Text Analysis Pipeline Contrast" width="100%" />
</p>

#### The Journey of a Character Stream
Imagine indexing a technical article containing the sentence: `The <div>replicating</div> cache nodes achieve high throughput.`

The raw character stream first encounters **Character Filters**. Before any word boundaries are detected, character filters operate on the level of individual glyphs. They strip out markup tags like `<div>` and `</div>`, decode HTML entities such as `&amp;` into `and`, and normalize non-standard Unicode characters. Crucially, character filters maintain an offset mapping back to the original text, ensuring that when Lucene highlights search snippets later, the highlighting reflects the exact character positions in the raw source document.

Next, the cleaned character stream passes into the **Tokenizer**. This stage defines the fundamental token granularity of the index. A standard tokenizer detects Unicode word boundaries according to international text segmentation standards (UAX #29), breaking the stream at whitespace and punctuation. At this stage, our sentence is partitioned into discrete token objects: `The`, `replicating`, `cache`, `nodes`, `achieve`, `high`, `throughput`.

The resulting sequence of tokens now enters a conveyor belt of **Token Filters**, where linguistic normalization takes place sequentially:
First, a **lowercase filter** folds every character to lowercase, transforming `The` to `the`. Without this step, a search for `"the"` would fail to match a sentence that begins with a capitalized `"The"`.
Next, a **stopword filter** discards ubiquitous grammatical glue words like `the`, `is`, `in`, and `at`. Because these terms appear in virtually every English document, their statistical entropy is near zero, yet keeping them in an inverted index would bloat posting lists by millions of redundant entries.
Finally, the remaining tokens pass through a morphological **stemmer**, such as the algorithmic Porter Stemmer. The stemmer strips inflectional suffixes, reducing `replicating`, `replication`, and `replicated` down to their shared morphological root: `replic`. Likewise, `nodes` is reduced to `node`.

By the end of this pipeline, the original sentence has been distilled into compact, linguistically normalized search keys: `replic`, `cach`, `node`, `achiev`, `high`, `throughput`. When a user later queries `"replicating"`, the query passes through this identical analysis chain, produces the stem `replic`, and finds the document immediately.

#### The Architectural Trap of Single Analyzers
In production systems design, selecting a single analyzer inevitably introduces a painful engineering trade-off:
If you configure an aggressive linguistic analyzer with Porter stemming, you maximize recall for natural language prose. However, that same stemmer dismantles exact technical terminology. A compound technical term like `in-memory` gets stripped of its hyphen and stemmed into `memori`, while `c++` loses its operators.
Conversely, if you configure a strict regex tokenizer like the one we built in Lab 1, you cleanly preserve hyphenated compounds (`in-memory`, `key-value`), but you sacrifice linguistic flexibility entirely. When an engineer searches for `"replicating cache nodes"`, the strict tokenizer fails to match documents containing `"replication"`.

Production search engines escape this compromise through **Multi-Fields**. Instead of forcing a single analyzer upon a field, Elasticsearch allows the exact same document text to be analyzed and indexed into multiple parallel inverted indexes within the same Lucene segment:

```json
"content": {
  "type": "text",
  "analyzer": "standard",
  "fields": {
    "english": { "type": "text", "analyzer": "english" },
    "exact":   { "type": "text", "analyzer": "custom_hyphen_analyzer" }
  }
}
```

Under this architecture, `content` indexes the text using standard Unicode word boundaries for generic searches. Simultaneously, `content.english` applies the Porter stemmer and stopword removal to handle conversational queries, while `content.exact` applies a hyphen-preserving regex tokenizer to capture technical compound terms. At query time, the search application can target specific fields or run multi-match queries that combine the high recall of stemmed fields with the high precision of exact technical matches.

---

### Layer 5: Candidate Retrieval & Scoring Mechanics

Once queries are tokenized, how does the engine find and score matching documents?

<p align="center">
  <img src="images/diagram-4-candidate-retrieval-and-scoring-funnel.svg" alt="Candidate Retrieval and Scoring Funnel" width="100%" />
</p>

#### Conjunctive Retrieval (Strict Boolean AND)
In Lab 1, your `SearchRanker` implemented candidate retrieval via two-pointer sorted posting list intersection:

```python
# Intersecting posting lists for terms [A, B]
p1, p2 = 0, 0
while p1 < len(list_a) and p2 < len(list_b):
    if list_a[p1][0] == list_b[p2][0]:
        matches.append(list_a[p1][0])
        p1 += 1
        p2 += 1
    elif list_a[p1][0] < list_b[p2][0]:
        p1 += 1
    else:
        p2 += 1
```

* **The Conjunctive Cliff**: If a user submits a 4-word query like `"redis cache clustering and sharding"`, Boolean AND requires a document to contain **all 4 terms**. If Document 1 contains `redis`, `cache`, and `clustering`, and Document 3 contains `sharding`, neither document matches. The intersection returns **0 results**, producing a catastrophic false negative.

#### Disjunctive Retrieval (Boolean OR with WAND Optimization)
Elasticsearch match queries default to `operator: "or"`. The engine evaluates the union of posting lists:
* Any document containing **at least one** query term enters the candidate pool.
* Scores from matching terms accumulate linearly:
  ```text
  Score(D, Q) = Sum_{t in Q} BM25(t, D)
  ```
* **The Weak AND (WAND) Pruning Optimization**: In a collection of 50 million documents, evaluating the full union of common terms would require scoring millions of poor matches. Lucene's WAND algorithm calculates the maximum possible BM25 contribution for each term (`max_score(t)`). During traversal, Lucene sorts posting iterators and dynamically skips past any document whose theoretical upper-bound score cannot beat the current K-th highest score in the priority queue.

#### Scoring Formula Contrast: Exact Float BM25 vs. Quantized Byte Norms
Both engines rank documents using the **Okapi BM25** formula:

```text
BM25(D, Q) = Sum_{t in Q} IDF(t) * [ (TF(t, D) * (k1 + 1)) / (TF(t, D) + k1 * (1 - b + b * (|D| / avgdl))) ]
```

Where `k1 = 1.2` controls term frequency saturation and `b = 0.75` controls document length normalization.

However, the storage mechanics diverge significantly:
* **Homemade Index**: Computes document length normalization dynamically in standard 64-bit IEEE 754 floating-point math, preserving exact score separation.
* **Elasticsearch / Lucene**: Storing an 8-byte float for document length across billions of documents would consume massive disk and RAM. Lucene table-quantizes document length normalization into a **single 1-byte lossy float** (`computeNorm` with 3 bits of mantissa and 5 bits of exponent). This 1-byte compression reduces storage by 87.5% while sacrificing less than 0.1% ranking precision on web-scale collections.

---

### Layer 6: Ingestion Lifecycle, Translog & The Write Amplification Tax

A fundamental law of storage systems engineering states that **disk fsync operations are far too slow for real-time document indexing**. Calling a synchronous `fsync` on every incoming document caps ingestion throughput at a few hundred writes per second due to storage controller and flash write overhead. Yet, keeping newly indexed documents exclusively in volatile application RAM creates an unacceptable risk: an unexpected power outage or kernel panic would permanently destroy uncommitted data.

<p align="center">
  <img src="images/diagram-2-nrt-segment-lifecycle-and-merges.svg" alt="Near-Real-Time Segment Lifecycle and Merges" width="100%" />
</p>

Elasticsearch and Lucene resolve this classic tension through an elegant two-track architecture that decouples search visibility from physical disk persistence.

#### The Dual Ingestion Path
When an indexing request reaches an Elasticsearch data node, the document is forked into two concurrent execution paths:
In memory, the document enters an active indexing buffer managed by a `DocumentsWriterPerThread` worker. Here, text analysis runs across CPU cores, and in-memory posting lists begin assembling.
Concurrently, the raw document payload is appended sequentially to an on-disk transaction log known as the **Translog**. Because sequential disk appends require minimal head movement and no index restructuring, the Translog acts as a high-speed, crash-resilient ledger that guarantees durability without degrading ingestion throughput.

#### The Near-Real-Time (NRT) Refresh Gate
In traditional databases, data becomes visible only after a full disk commit. In search engines, waiting for disk commits would introduce unacceptable latency for users expecting rapid feedback.

Elasticsearch bridges this gap with the **Near-Real-Time (NRT) Refresh**:
By default, once every second (governed by `index.refresh_interval`), Elasticsearch triggers a lightweight refresh. The contents of the in-memory indexing buffer are written out into a new immutable Lucene segment. Crucially, this segment is written only to the **operating system Page Cache** in RAM—no physical `fsync` is called.
Immediately following this write, Elasticsearch opens an updated point-in-time reader (`IndexSearcher`) pointing to the newly opened segment. At that exact microsecond, the document becomes searchable across the cluster.

This architecture explains why Elasticsearch is described as "near-real-time" rather than strictly real-time: a document indexed at millisecond 100 will not appear in search results until the 1-second refresh boundary triggers at millisecond 1000, unless the client explicitly passes `refresh=true` to force a synchronous refresh gate.

#### The Flush: Physical Persistence and Translog Truncation
While the refresh gate satisfies low-latency search visibility, physical durability remains unfulfilled until a **Flush** takes place.
Every 30 minutes, or whenever the Translog grows past 512 megabytes, Elasticsearch triggers a Lucene commit. During a flush, the engine issues a blocking `fsync` call across all open segment files residing in the OS page cache, forcing physical disk controllers to flush their write caches to non-volatile flash storage. Once all segments are safely persisted to disk, an updated commit marker (`segments_N`) is written, and the Translog is safely truncated. If the server loses power before a flush, Elasticsearch reboots, inspects the last valid commit marker, and replays all missing operations directly from the Translog into new segments.

#### Immutable Segments, Deletions, and the Merge Tax
Because Lucene segments are strictly write-once and immutable, Elasticsearch never modifies an existing segment file when a document is updated or deleted. Modifying posting lists in place would destroy bit-packed SIMD encodings and require massive on-disk shifts.

Instead, when a document is deleted or replaced by a newer version, Lucene appends the new document to the active buffer and writes a tombstone bit for the older DocID into a companion bitset file called `.del`. During query execution, the `IndexSearcher` evaluates posting lists normally and simply masks out any DocIDs flagged in the `.del` bitmap.

As indexing continues, a shard accumulates dozens of small, independently created segment files. Searching across too many segments degrades query performance, as each query must poll every segment separately. To solve this, background threads governed by the `TieredMergePolicy` continuously monitor segment sizes. When several similarly-sized segments accumulate, the merge policy combines their posting lists into a single, unified larger segment. During this merge operation, documents marked as deleted in the `.del` bitset are permanently dropped, reclaiming disk capacity. This periodic background consolidation represents a controlled **write amplification tax**, paid deliberately to preserve fast, deterministic query performance.

---

## Stage 3: Lab Architecture & Environment Preparation

---

### Step 3.1: Workspace Setup

In your terminal, navigate to your workspace and create the project directory:

```bash
mkdir -p es_benchmark_lab && cd es_benchmark_lab
```

---

### Step 3.2: Copying Foundational Lab 1 Code (Category A Assets)

Copy the 4 foundational files from Module 16a, Lab 1 into your `es_benchmark_lab` directory:
* Source Repository: [Lab 1 Code Assets Repository](https://github.com/Mahboob-A/lab-assets/tree/main/system-design-s3/m16a-distributed-search-and-typehead/m16a-l1-inverted-index/code-implementation)

Ensure your directory contains:
1. `corpus.py`: The 9 distributed systems seed documents.
2. `analyzer.py`: The `TextAnalyzer` class.
3. `index.py`: The `InvertedIndex` class.
4. `ranker.py`: The `SearchRanker` class.

Verify the files are present:

```bash
ls -la corpus.py analyzer.py index.py ranker.py
```

<p align="center">
  <img src="images/ts-01-project-setup.png" alt="Project-Setup" width="100%" />
</p>


---

### Step 3.3: Docker Compose Configuration

Create `docker-compose.yml` to launch a single-node Elasticsearch instance configured for fast local benchmarking:

```yaml
version: "3.8"

services:
  elasticsearch:
    image: docker.elastic.co/elasticsearch/elasticsearch:8.15.0
    container_name: poridhi-elasticsearch-m16a-l3
    environment:
      - discovery.type=single-node
      - xpack.security.enabled=false
      - xpack.security.http.ssl.enabled=false
      - ES_JAVA_OPTS=-Xms512m -Xmx512m
    ports:
      - "9200:9200"
    mem_limit: 1.5g
    ulimits:
      memlock:
        soft: -1
        hard: -1
      nofile:
        soft: 65536
        hard: 65536
    healthcheck:
      test: ["CMD-SHELL", "curl -s http://localhost:9200/_cluster/health | grep -q '\"status\":\"green\"\\|\"status\":\"yellow\"'"]
      interval: 5s
      timeout: 5s
      retries: 12
      start_period: 15s
```

Launch the Elasticsearch container:

```bash
docker compose up -d
```

<p align="center">
  <img src="images/ts-02-docker-compose-container.png" alt="Docker-Compose-Container" width="100%" />
</p>

---

### Step 3.4: Installing the Compatible Python Client Driver

Install the official Python Elasticsearch client targeting version 8 compatibility:

```bash
pip install "elasticsearch<9.0.0"
```

> [!NOTE]
> Installing `elasticsearch<9.0.0` ensures the client sends HTTP headers compatible with Elasticsearch 8.x servers (`application/vnd.elasticsearch+json; compatible-with=8`), preventing HTTP 400 media-type header exceptions.

---

### Step 3.5: Verifying Cluster Connectivity (`test_es.py`)

Create `test_es.py` to poll the cluster health endpoint until the instance reports `GREEN` status:

```python
import json
import time
import urllib.request

ES_URL = "http://localhost:9200"

def verify_cluster(max_retries: int = 15, delay: int = 2) -> bool:
    print(f"Polling Elasticsearch cluster health at {ES_URL}...")
    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.urlopen(f"{ES_URL}/_cluster/health", timeout=3)
            if req.status == 200:
                data = json.loads(req.read().decode("utf-8"))
                status = data.get("status", "unknown")
                cluster_name = data.get("cluster_name", "unknown")
                nodes = data.get("number_of_nodes", 0)

                print(f"[SUCCESS] Cluster responded on attempt {attempt}!")
                print(f"  * Cluster Name:     {cluster_name}")
                print(f"  * Cluster Status:   {status.upper()}")
                print(f"  * Active Nodes:     {nodes}")
                return True
        except Exception as e:
            print(f"  [Attempt {attempt}/{max_retries}] Waiting for Elasticsearch... ({e})")
            time.sleep(delay)

    print("[ERROR] Could not connect to Elasticsearch.")
    return False

if __name__ == "__main__":
    verify_cluster()
```

Run the connectivity test:

```bash
python3 test_es.py
```

<p align="center">
  <img src="images/ts-04-test_es_py.png" alt="Test-ES" width="100%" />
</p>

---

## Stage 4: Step-by-Step Hands-On Implementation

> [!CAUTION]
> **Before Writing Code: Try Implementing the # TODO Sections First!**
> Do not jump straight to the solutions. For each component below, make an honest attempt to complete the `# TODO:` challenges on your own before expanding the solution code. Wrestling with index mappings, multi-fields, and Information Retrieval metrics is where true engineering intuition develops. Complete, verified solution implementations are provided immediately after each challenge block.
> Reference solution code is available at: [Lab 3 Reference Solution Repository](https://github.com/Mahboob-A/lab-assets/tree/main/system-design-s3/m16a-distributed-search-and-typehead/m16a-l3-elasticsearch-comparison/code-implementation)

---

### Milestone 1: Pre-Populating Category A Assets from Lab 1

Confirm that your pre-populated files from Lab 1 load the 9 documents properly:

```bash
python3 -c "from corpus import DOCUMENTS; print(f'Loaded {len(DOCUMENTS)} documents from corpus.py.')"
```

Expected output:
```text
Loaded 9 documents from corpus.py.
```

---

### Milestone 2: Spinning Up Elasticsearch & Mapping Multi-Fields (`es_client.py`)

Create `es_client.py`. This class connects to Elasticsearch, configures our multi-field mappings, bulk ingests the corpus, and enforces the Near-Real-Time refresh gate.

#### The Challenge: Scaffolding `es_client.py`

Write the skeleton of `es_client.py` with the following `# TODO:` blocks:

```python
"""
es_client.py - Elasticsearch Client & Multi-Field Mapping Engine.
"""

from typing import Any
import corpus
from elasticsearch import Elasticsearch, helpers

INDEX_NAME = "distributed_systems_corpus"


class ElasticsearchClient:
    def __init__(self, host: str = "http://localhost:9200"):
        self.host = host
        self.client = Elasticsearch(
            hosts=[host],
            verify_certs=False,
            request_timeout=10,
            retry_on_timeout=True,
            max_retries=3,
        )

    def ping(self) -> bool:
        try:
            return bool(self.client.ping())
        except Exception:
            return False

    def create_index(self, recreate: bool = True) -> bool:
        if self.client.indices.exists(index=INDEX_NAME):
            if recreate:
                self.client.indices.delete(index=INDEX_NAME)
                print(f"[INDEX] Deleted existing index '{INDEX_NAME}'.")
            else:
                return True

        # TODO 1: Define the mapping_body with:
        # 1. Single primary shard (number_of_shards: 1, number_of_replicas: 0).
        # 2. A custom analyzer named "custom_hyphen_analyzer" using pattern tokenizer r"[^a-zA-Z0-9_-]+" and lowercase filter.
        # 3. Properties:
        #    - doc_id: integer
        #    - title: text with subfield "english" using english analyzer
        #    - content: text with standard analyzer, subfield "english" (english analyzer), and subfield "exact" (custom_hyphen_analyzer)
        mapping_body = {}

        self.client.indices.create(index=INDEX_NAME, body=mapping_body)
        print(f"[INDEX] Created index '{INDEX_NAME}' with multi-field mappings.")
        return True

    def index_corpus(self, docs: dict[int, dict[str, str]]) -> int:
        actions = []
        for doc_id, doc in docs.items():
            action = {
                "_index": INDEX_NAME,
                "_id": str(doc_id),
                "_source": {
                    "doc_id": doc_id,
                    "title": doc["title"],
                    "content": doc["content"],
                },
            }
            actions.append(action)

        success, _ = helpers.bulk(self.client, actions)

        # TODO 2: Implement the Near-Real-Time (NRT) Refresh Gate.
        # Call explicit refresh on INDEX_NAME so in-memory buffers flush into searchable OS page-cache segments.

        return success

    def search(
        self,
        query: str,
        field: str = "content",
        operator: str = "or",
        top_k: int = 10,
    ) -> list[tuple[int, float, str]]:
        query_body: dict[str, Any] = {
            "query": {
                "match": {
                    field: {
                        "query": query,
                        "operator": operator,
                    }
                }
            },
            "size": top_k,
        }

        response = self.client.search(index=INDEX_NAME, body=query_body)
        hits = response["hits"]["hits"]

        results = []
        for hit in hits:
            doc_id = hit["_source"]["doc_id"]
            score = round(float(hit["_score"]), 4)
            title = hit["_source"]["title"]
            results.append((doc_id, score, title))

        return results

    def explain(
        self, query: str, doc_id: int, field: str = "content.english"
    ) -> dict:
        query_body = {"query": {"match": {field: query}}}
        return self.client.explain(
            index=INDEX_NAME, id=str(doc_id), body=query_body
        )
```

<details>
<summary>Click to view complete solution for es_client.py</summary>

```python
"""
es_client.py - Elasticsearch Client & Multi-Field Mapping Engine (Solution).
"""

from typing import Any
import corpus
from elasticsearch import Elasticsearch, helpers

INDEX_NAME = "distributed_systems_corpus"


class ElasticsearchClient:
    def __init__(self, host: str = "http://localhost:9200"):
        self.host = host
        self.client = Elasticsearch(
            hosts=[host],
            verify_certs=False,
            request_timeout=10,
            retry_on_timeout=True,
            max_retries=3,
        )

    def ping(self) -> bool:
        try:
            return bool(self.client.ping())
        except Exception:
            return False

    def create_index(self, recreate: bool = True) -> bool:
        if self.client.indices.exists(index=INDEX_NAME):
            if recreate:
                self.client.indices.delete(index=INDEX_NAME)
                print(f"[INDEX] Deleted existing index '{INDEX_NAME}'.")
            else:
                return True

        mapping_body = {
            "settings": {
                "number_of_shards": 1,
                "number_of_replicas": 0,
                "analysis": {
                    "analyzer": {
                        "custom_hyphen_analyzer": {
                            "type": "custom",
                            "tokenizer": "custom_hyphen_tokenizer",
                            "filter": ["lowercase"],
                        }
                    },
                    "tokenizer": {
                        "custom_hyphen_tokenizer": {
                            "type": "pattern",
                            "pattern": r"[^a-zA-Z0-9_-]+",
                        }
                    },
                },
            },
            "mappings": {
                "properties": {
                    "doc_id": {"type": "integer"},
                    "title": {
                        "type": "text",
                        "fields": {
                            "english": {"type": "text", "analyzer": "english"}
                        },
                    },
                    "content": {
                        "type": "text",
                        "analyzer": "standard",
                        "fields": {
                            "english": {
                                "type": "text",
                                "analyzer": "english",
                            },
                            "exact": {
                                "type": "text",
                                "analyzer": "custom_hyphen_analyzer",
                            },
                        },
                    },
                }
            },
        }

        self.client.indices.create(index=INDEX_NAME, body=mapping_body)
        print(f"[INDEX] Created index '{INDEX_NAME}' with multi-field mappings.")
        return True

    def index_corpus(self, docs: dict[int, dict[str, str]]) -> int:
        actions = []
        for doc_id, doc in docs.items():
            action = {
                "_index": INDEX_NAME,
                "_id": str(doc_id),
                "_source": {
                    "doc_id": doc_id,
                    "title": doc["title"],
                    "content": doc["content"],
                },
            }
            actions.append(action)

        success, _ = helpers.bulk(self.client, actions)

        # Enforce NRT Refresh Gate: Commit buffers into searchable OS page-cache segments
        self.client.indices.refresh(index=INDEX_NAME)
        print(f"[INGEST] Successfully indexed {success} documents and executed explicit refresh.")
        return success

    def search(
        self,
        query: str,
        field: str = "content",
        operator: str = "or",
        top_k: int = 10,
    ) -> list[tuple[int, float, str]]:
        query_body: dict[str, Any] = {
            "query": {
                "match": {
                    field: {
                        "query": query,
                        "operator": operator,
                    }
                }
            },
            "size": top_k,
        }

        response = self.client.search(index=INDEX_NAME, body=query_body)
        hits = response["hits"]["hits"]

        results = []
        for hit in hits:
            doc_id = hit["_source"]["doc_id"]
            score = round(float(hit["_score"]), 4)
            title = hit["_source"]["title"]
            results.append((doc_id, score, title))

        return results

    def explain(
        self, query: str, doc_id: int, field: str = "content.english"
    ) -> dict:
        query_body = {"query": {"match": {field: query}}}
        return self.client.explain(
            index=INDEX_NAME, id=str(doc_id), body=query_body
        )


if __name__ == "__main__":
    client = ElasticsearchClient()
    if not client.ping():
        print("[ERROR] Cannot reach Elasticsearch on localhost:9200.")
        exit(1)

    print("[SUCCESS] Connected to Elasticsearch!")
    client.create_index(recreate=True)
    client.index_corpus(corpus.DOCUMENTS)

    # Sanity search test
    test_query = "replicating cache nodes"
    print(f"\nRunning Sanity Test Query: '{test_query}'")
    hits_std = client.search(test_query, field="content", operator="or")
    hits_eng = client.search(test_query, field="content.english", operator="or")

    print(f"  * Standard Analyzer Hits: {len(hits_std)}")
    for doc_id, score, title in hits_std[:2]:
        print(f"      Doc {doc_id} [Score: {score}] - {title}")

    print(f"  * English Analyzer (Stemmed) Hits: {len(hits_eng)}")
    for doc_id, score, title in hits_eng[:2]:
        print(f"      Doc {doc_id} [Score: {score}] - {title}")
```
</details>

Run `es_client.py`:

```bash
python3 es_client.py
```

<p align="center">
  <img src="images/ts-05-es_client_py.png" alt="Test-ES-Client" width="100%" />
</p>

Notice what happened in the sanity test output:
* When queried against `content` (Standard Analyzer), Document 1 was not in the top 2 hits because `"replicating"` did not stem to match `"replication"`.
* When queried against `content.english` (English Analyzer with Porter Stemmer), Document 1 surged to Rank 1 with a BM25 score of 3.105, and Document 8 reached Rank 2 with a score of 2.3188.

---

### Milestone 3: Encapsulating the Homemade Inverted Index (`homemade_adapter.py`)

Create `homemade_adapter.py`. This adapter wraps our Lab 1 components (`TextAnalyzer`, `InvertedIndex`, and `SearchRanker`) into a uniform search engine interface compatible with Elasticsearch's output dictionary format.

#### The Challenge: Scaffolding `homemade_adapter.py`

Write `homemade_adapter.py` with the following `# TODO:` blocks:

```python
"""
homemade_adapter.py - Adapter for the Custom Inverted Index from Lab 1.
"""

from analyzer import TextAnalyzer
from corpus import DOCUMENTS
from index import InvertedIndex
from ranker import SearchRanker


class HomemadeSearchEngine:
    def __init__(self, documents: dict[int, dict] = None):
        self.documents = documents if documents is not None else DOCUMENTS
        self.analyzer = TextAnalyzer()
        self.index = InvertedIndex()
        self._build_index()
        self.ranker = SearchRanker(self.index, self.analyzer)

    def _build_index(self) -> None:
        # TODO 1: Iterate over self.documents.items().
        # Concatenate title and content: f"{doc_data['title']} {doc_data['content']}".
        # Tokenize using self.analyzer.analyze() and add to self.index using add_document(doc_id, tokens).
        pass

    def search(
        self,
        query: str,
        top_k: int = 10,
        mode: str = "bm25",
        k1: float = 1.2,
        b: float = 0.75,
    ) -> list[dict]:
        raw_results = self.ranker.search(
            query=query,
            mode=mode,
            top_k=top_k,
            k1=k1,
            b=b,
        )

        results = []
        # TODO 2: Transform raw (doc_id, score) tuples into standardized dictionaries:
        # {
        #     "doc_id": doc_id,
        #     "score": round(score, 4),
        #     "title": doc_data.get("title", ""),
        #     "content_preview": content[:80] + "...",
        # }
        return results

    def explain(self, query: str, doc_id: int) -> dict:
        return self.ranker.explain_score(query, doc_id)
```

<details>
<summary>Click to view complete solution for homemade_adapter.py</summary>

```python
"""
homemade_adapter.py - Adapter for the Custom Inverted Index from Lab 1 (Solution).
"""

from analyzer import TextAnalyzer
from corpus import DOCUMENTS
from index import InvertedIndex
from ranker import SearchRanker


class HomemadeSearchEngine:
    def __init__(self, documents: dict[int, dict] = None):
        self.documents = documents if documents is not None else DOCUMENTS
        self.analyzer = TextAnalyzer()
        self.index = InvertedIndex()
        self._build_index()
        self.ranker = SearchRanker(self.index, self.analyzer)

    def _build_index(self) -> None:
        for doc_id, doc_data in self.documents.items():
            combined_text = f"{doc_data['title']} {doc_data['content']}"
            tokens = self.analyzer.analyze(combined_text)
            self.index.add_document(doc_id, tokens)

    def search(
        self,
        query: str,
        top_k: int = 10,
        mode: str = "bm25",
        k1: float = 1.2,
        b: float = 0.75,
    ) -> list[dict]:
        raw_results = self.ranker.search(
            query=query,
            mode=mode,
            top_k=top_k,
            k1=k1,
            b=b,
        )

        results = []
        for doc_id, score in raw_results:
            doc_data = self.documents.get(doc_id, {})
            title = doc_data.get("title", f"Document #{doc_id}")
            content = doc_data.get("content", "")
            preview = content[:80] + "..." if len(content) > 80 else content
            results.append(
                {
                    "doc_id": doc_id,
                    "score": round(score, 4),
                    "title": title,
                    "content_preview": preview,
                }
            )
        return results

    def explain(self, query: str, doc_id: int) -> dict:
        return self.ranker.explain_score(query, doc_id)


if __name__ == "__main__":
    engine = HomemadeSearchEngine()
    print("=" * 65)
    print("Self-Test: HomemadeSearchEngine Adapter")
    print("=" * 65)
    print(f"Corpus size: {engine.index.total_docs} documents")
    print(f"Vocabulary:  {engine.index.vocabulary_size()} unique terms")
    print(f"Avg doc len: {engine.index.avg_doc_len:.2f} tokens")
    print("-" * 65)

    test_queries = [
        "raft consensus leader election",
        "in-memory key-value",
        "replicating cache nodes",
    ]

    for q in test_queries:
        hits = engine.search(q, top_k=3)
        print(f"Query: '{q}' -> Hits: {len(hits)}")
        for i, hit in enumerate(hits, 1):
            print(f"  [{i}] Doc {hit['doc_id']}: score={hit['score']} - {hit['title']}")
        if not hits:
            print("  (0 matches - Boolean AND required all terms)")
        print()
```
</details>

Run `homemade_adapter.py`:

```bash
python3 homemade_adapter.py
```

<p align="center">
  <img src="images/ts-06-homemade_adapter_py.png" alt="Test-Homemade-Adapter" width="100%" />
</p>

---

### Milestone 4: The Cranfield Evaluation Paradigm & Relevance Ground Truth (`generate_judgments.py`)

In software engineering, testing a database query is binary: either the row with `id = 42` exists, or it does not.

In Information Retrieval science, search relevance is **probabilistic**. A document can be partially relevant, tangentially related, or authoritative. How do search engineers evaluate whether an algorithm change actually improved ranking quality across millions of users?

They use the **Cranfield Evaluation Paradigm** (established in the 1960s by Cyril Cleverdon and formalized by NIST's Text REtrieval Conference, TREC). An evaluation suite requires three components:
1. A static document collection (our `corpus.py`).
2. A representative query workload (our 6 benchmark queries).
3. **Ground-Truth Relevance Judgments**: Human domain experts annotate each document for each query on a graded relevance scale:
   * **Grade 0 (Irrelevant)**: Does not satisfy the user's information need.
   * **Grade 1 (Partially Relevant)**: Touches on related sub-topics or background context.
   * **Grade 2 (Highly Relevant)**: Directly answers and centers on the core query topic.

Rather than manually authoring a 120-line JSON document by hand, create `generate_judgments.py` to programmatically build `relevance_judgments.json`:

```python
"""
generate_judgments.py - Ground Truth Relevance Judgment Generator.
"""

import json
from pathlib import Path

JUDGMENTS_DATA = {
    "metadata": {
        "framework": "Cranfield / TREC Information Retrieval Evaluation Paradigm",
        "purpose": "Ground truth relevance annotations for Dual-Engine Benchmark Comparison",
        "scale": {
            "0": "Irrelevant",
            "1": "Partially Relevant",
            "2": "Highly Relevant",
        },
    },
    "queries": [
        {
            "query_id": "A1",
            "query": "replicating cache nodes",
            "category": "Elasticsearch Win (Morphological Suffix Stemming)",
            "expected_top_docs": [1, 8],
            "judgments": [
                {"doc_id": 1, "grade": 2, "notes": "Covers distributed cache nodes and replication."},
                {"doc_id": 8, "grade": 1, "notes": "Covers data replication strategies (sync vs async)."},
                {"doc_id": 9, "grade": 1, "notes": "Verbose document mentioning cache cluster repeatedly."},
            ],
        },
        {
            "query_id": "A2",
            "query": "redis cache clustering and sharding",
            "category": "Elasticsearch Win (Disjunctive Multi-Term Candidate Retrieval)",
            "expected_top_docs": [1, 3],
            "judgments": [
                {"doc_id": 1, "grade": 2, "notes": "Covers Redis in-memory cache and cluster acceleration."},
                {"doc_id": 3, "grade": 2, "notes": "Dedicated document on database sharding and partitioning."},
                {"doc_id": 5, "grade": 1, "notes": "Consistent hashing for cache server distribution."},
            ],
        },
        {
            "query_id": "A3",
            "query": "how to achieve consensus across distributed servers",
            "category": "Elasticsearch Win (Conversational Stopword Filtering & IDF)",
            "expected_top_docs": [2],
            "judgments": [
                {"doc_id": 2, "grade": 2, "notes": "Explains Raft consensus across distributed servers."},
                {"doc_id": 6, "grade": 1, "notes": "Covers distributed transactions and 2PC coordinator."},
            ],
        },
        {
            "query_id": "B1",
            "query": "in-memory key-value",
            "category": "Homemade Index Win (Hyphenated Compound Term Preservation)",
            "expected_top_docs": [1],
            "judgments": [
                {"doc_id": 1, "grade": 2, "notes": "Introduces Redis as an in-memory key-value store."}
            ],
        },
        {
            "query_id": "B2",
            "query": "scatter-gather",
            "category": "Homemade Index Win (Small-Corpus Exact Float IDF Separation)",
            "expected_top_docs": [3],
            "judgments": [
                {"doc_id": 3, "grade": 2, "notes": "Covers cross-shard scatter-gather queries in sharded databases."}
            ],
        },
        {
            "query_id": "B3",
            "query": "raft consensus leader election",
            "category": "Homemade Index Win (Microsecond Zero-Overhead RAM Execution)",
            "expected_top_docs": [2],
            "judgments": [
                {"doc_id": 2, "grade": 2, "notes": "Core document on Raft consensus and leader election."},
                {"doc_id": 6, "grade": 1, "notes": "Mentions distributed coordination for two-phase commit."},
            ],
        },
    ],
}

def generate_judgments_file(output_path: str = "relevance_judgments.json") -> None:
    path = Path(__file__).parent / output_path
    with open(path, "w", encoding="utf-8") as f:
        json.dump(JUDGMENTS_DATA, f, indent=2)
    print(f"[SUCCESS] Generated ground-truth relevance judgments: {path.name}")
    print(f"          Total queries annotated: {len(JUDGMENTS_DATA['queries'])}")

if __name__ == "__main__":
    generate_judgments_file()
```

Run `generate_judgments.py`:

```bash
python3 generate_judgments.py
```

Expected output:

<p align="center">
  <img src="images/ts-07-generate_judgements_py.png" alt="Generate-Judgements" width="100%" />
</p>

---

### Milestone 5: Building the IR Evaluation Engine (`evaluator.py`)

<p align="center">
  <img src="images/diagram-6-ir-evaluation-metrics-pipeline.svg" alt="Information Retrieval Evaluation Metrics Pipeline" width="100%" />
</p>

In standard machine learning classification, engineers evaluate models using overall accuracy (correct predictions divided by total predictions). However, in full-text search systems, evaluating quality requires an entirely different mathematical approach known as **Information Retrieval (IR) Evaluation**.

Why does classification accuracy fail in search?
1. In search, the non-relevant documents outnumber relevant documents by thousands or millions to one. An engine that returns an empty list could achieve 99.99% accuracy on non-relevance while failing completely at its primary purpose.
2. In search, **order matters decisively**. Human users rarely scroll past the top 3 to 5 results. A search engine that returns the authoritative document at position 50 is functionally identical to an engine that failed to find it at all.

To quantify ranking quality rigorously, the Information Retrieval community developed standardized metrics that evaluate relevance, coverage, and rank order:

#### 1. Precision@k (P@k): Result Purity within the User's Attention Window
`Precision@k` answers: *"Of the top k documents presented to the user, what proportion was actually relevant?"*

```text
Precision@k = (Count of relevant docs in top k) / k
```

If a user inspects the top 3 hits, and 2 of those hits are relevant while 1 is irrelevant, `Precision@3 = 2 / 3 = 0.6667`. Evaluating precision at fixed cutoffs (such as k=1, 3, or 5) directly reflects the user experience in modern web search viewports.

#### 2. Recall@k (R@k): System Completeness Across the Global Corpus
Precision alone can be deceptively high: if a system retrieves only 1 document and that document happens to be relevant, `Precision@1 = 1.0` (100%), even if 9 other critical documents were completely missed.

`Recall@k` counterbalances this by measuring coverage: *"Of all the relevant documents that exist in the entire collection, what fraction did the engine pull into the top k?"*

```text
Recall@k = (Count of relevant docs in top k) / (Total relevant docs in corpus)
```

If a query has 4 relevant documents in the corpus and the engine retrieves 2 of them within the top 3 hits, `Recall@3 = 2 / 4 = 0.5000`.

#### 3. Reciprocal Rank (RR): Time to First Satisfaction
In web search and transactional lookups, users often seek a single correct answer (such as finding the official documentation for a library). `Reciprocal Rank` measures how quickly the user encounters their very first relevant hit:

```text
RR = 1 / rank_of_first_relevant_doc
```

If the very first hit (rank 1) is relevant, `RR = 1 / 1 = 1.0`. If the engine buries the first relevant hit at rank 2, `RR = 1 / 2 = 0.5`. If the first relevant hit is at rank 4, `RR = 0.25`. If no relevant document is found in the retrieved list, `RR = 0.0`.

When averaged across a test workload of multiple queries, this produces **Mean Reciprocal Rank (MRR)**, summarizing how reliably the engine puts a correct answer in the first position.

#### 4. Average Precision (AP): The Gold Standard for Ranked Lists
While Precision@k checks a static cutoff, and RR only looks at the first relevant hit, real users care about the quality of the entire ranked sequence. A system that places relevant documents at ranks 1 and 2 is far superior to a system that places them at ranks 2 and 3, even though both achieve the identical `Precision@3`.

`Average Precision` calculates the precision at each rank where a relevant document is retrieved, and averages those precisions over the total count of known relevant documents:

```text
AP = (1 / |Relevant|) * Sum_{k=1}^N (Precision@k * rel(k))
```

Where `rel(k)` is a binary indicator (1 if the document at rank k is relevant, 0 otherwise). This mathematical formulation heavily penalizes ranking algorithms that push relevant documents down the page.

When averaged across an entire benchmark suite, the macro-average is **Mean Average Precision (MAP)**—the single most respected metric for search engine quality.

In `evaluator.py`, you will implement these metrics from first principles.

#### The Challenge: Scaffolding `evaluator.py`

Write `evaluator.py` with the following `# TODO:` blocks:

```python
"""
evaluator.py - Information Retrieval Evaluation Engine.
"""

import json
from pathlib import Path


class SearchEvaluator:
    def __init__(self, judgments_path: str = None):
        if judgments_path is None:
            judgments_path = str(Path(__file__).parent / "relevance_judgments.json")
        self.judgments_path = judgments_path
        self.data = self._load_judgments()
        self.queries_map = {q["query"]: q for q in self.data.get("queries", [])}
        self.query_id_map = {q["query_id"]: q for q in self.data.get("queries", [])}

    def _load_judgments(self) -> dict:
        with open(self.judgments_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def get_relevant_docs(self, query: str, min_grade: int = 1) -> set[int]:
        query_entry = self.queries_map.get(query) or self.query_id_map.get(query)
        if not query_entry:
            return set()
        return {
            j["doc_id"]
            for j in query_entry.get("judgments", [])
            if j.get("grade", 0) >= min_grade
        }

    @staticmethod
    def precision_at_k(retrieved: list[int], relevant: set[int], k: int) -> float:
        # TODO 1A: Compute Precision@k = (count of relevant docs in retrieved[:k]) / k
        return 0.0

    @staticmethod
    def recall_at_k(retrieved: list[int], relevant: set[int], k: int) -> float:
        # TODO 1B: Compute Recall@k = (count of relevant docs in retrieved[:k]) / len(relevant)
        return 0.0

    @staticmethod
    def reciprocal_rank(retrieved: list[int], relevant: set[int]) -> float:
        # TODO 2A: Find the 1-based rank of the first doc in retrieved that is in relevant.
        # Return 1.0 / rank. If no hit, return 0.0.
        return 0.0

    @staticmethod
    def average_precision(retrieved: list[int], relevant: set[int]) -> float:
        # TODO 2B: For each rank k where retrieved[k-1] in relevant, add (hits / k).
        # Divide the sum by len(relevant).
        return 0.0

    def evaluate_retrieval(
        self,
        query: str,
        retrieved: list[int],
        k_values: list[int] = None,
        min_grade: int = 1,
    ) -> dict:
        if k_values is None:
            k_values = [1, 3, 5, 10]
        relevant = self.get_relevant_docs(query, min_grade=min_grade)

        metrics = {
            "query": query,
            "retrieved_count": len(retrieved),
            "relevant_count": len(relevant),
            "retrieved_doc_ids": retrieved,
            "relevant_doc_ids": sorted(list(relevant)),
            "reciprocal_rank": self.reciprocal_rank(retrieved, relevant),
            "average_precision": self.average_precision(retrieved, relevant),
        }
        for k in k_values:
            metrics[f"precision@{k}"] = self.precision_at_k(retrieved, relevant, k)
            metrics[f"recall@{k}"] = self.recall_at_k(retrieved, relevant, k)
        return metrics

    def aggregate_suite(self, query_metrics: list[dict]) -> dict:
        if not query_metrics:
            return {}
        n = len(query_metrics)
        map_score = sum(m.get("average_precision", 0.0) for m in query_metrics) / n
        mrr_score = sum(m.get("reciprocal_rank", 0.0) for m in query_metrics) / n

        summary = {
            "query_count": n,
            "mean_average_precision": round(map_score, 4),
            "mean_reciprocal_rank": round(mrr_score, 4),
        }
        for k in [1, 3, 5, 10]:
            p_key = f"precision@{k}"
            r_key = f"recall@{k}"
            if p_key in query_metrics[0]:
                summary[f"mean_{p_key}"] = round(
                    sum(m.get(p_key, 0.0) for m in query_metrics) / n, 4
                )
            if r_key in query_metrics[0]:
                summary[f"mean_{r_key}"] = round(
                    sum(m.get(r_key, 0.0) for m in query_metrics) / n, 4
                )
        return summary
```

<details>
<summary>Click to view complete solution for evaluator.py</summary>

```python
"""
evaluator.py - Information Retrieval Evaluation Engine (Solution).
"""

import json
from pathlib import Path


class SearchEvaluator:
    def __init__(self, judgments_path: str = None):
        if judgments_path is None:
            judgments_path = str(Path(__file__).parent / "relevance_judgments.json")
        self.judgments_path = judgments_path
        self.data = self._load_judgments()
        self.queries_map = {q["query"]: q for q in self.data.get("queries", [])}
        self.query_id_map = {q["query_id"]: q for q in self.data.get("queries", [])}

    def _load_judgments(self) -> dict:
        with open(self.judgments_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def get_relevant_docs(self, query: str, min_grade: int = 1) -> set[int]:
        query_entry = self.queries_map.get(query) or self.query_id_map.get(query)
        if not query_entry:
            return set()
        return {
            j["doc_id"]
            for j in query_entry.get("judgments", [])
            if j.get("grade", 0) >= min_grade
        }

    @staticmethod
    def precision_at_k(retrieved: list[int], relevant: set[int], k: int) -> float:
        if k <= 0 or not retrieved:
            return 0.0
        top_k = retrieved[:k]
        hits = sum(1 for doc_id in top_k if doc_id in relevant)
        return round(hits / k, 4)

    @staticmethod
    def recall_at_k(retrieved: list[int], relevant: set[int], k: int) -> float:
        if not relevant or k <= 0 or not retrieved:
            return 0.0
        top_k = retrieved[:k]
        hits = sum(1 for doc_id in top_k if doc_id in relevant)
        return round(hits / len(relevant), 4)

    @staticmethod
    def reciprocal_rank(retrieved: list[int], relevant: set[int]) -> float:
        if not relevant:
            return 0.0
        for rank, doc_id in enumerate(retrieved, start=1):
            if doc_id in relevant:
                return round(1.0 / rank, 4)
        return 0.0

    @staticmethod
    def average_precision(retrieved: list[int], relevant: set[int]) -> float:
        if not relevant or not retrieved:
            return 0.0
        hits = 0
        sum_precisions = 0.0
        for rank, doc_id in enumerate(retrieved, start=1):
            if doc_id in relevant:
                hits += 1
                sum_precisions += hits / rank
        return round(sum_precisions / len(relevant), 4)

    def evaluate_retrieval(
        self,
        query: str,
        retrieved: list[int],
        k_values: list[int] = None,
        min_grade: int = 1,
    ) -> dict:
        if k_values is None:
            k_values = [1, 3, 5, 10]
        relevant = self.get_relevant_docs(query, min_grade=min_grade)

        metrics = {
            "query": query,
            "retrieved_count": len(retrieved),
            "relevant_count": len(relevant),
            "retrieved_doc_ids": retrieved,
            "relevant_doc_ids": sorted(list(relevant)),
            "reciprocal_rank": self.reciprocal_rank(retrieved, relevant),
            "average_precision": self.average_precision(retrieved, relevant),
        }
        for k in k_values:
            metrics[f"precision@{k}"] = self.precision_at_k(retrieved, relevant, k)
            metrics[f"recall@{k}"] = self.recall_at_k(retrieved, relevant, k)
        return metrics

    def aggregate_suite(self, query_metrics: list[dict]) -> dict:
        if not query_metrics:
            return {}
        n = len(query_metrics)
        map_score = sum(m.get("average_precision", 0.0) for m in query_metrics) / n
        mrr_score = sum(m.get("reciprocal_rank", 0.0) for m in query_metrics) / n

        summary = {
            "query_count": n,
            "mean_average_precision": round(map_score, 4),
            "mean_reciprocal_rank": round(mrr_score, 4),
        }
        for k in [1, 3, 5, 10]:
            p_key = f"precision@{k}"
            r_key = f"recall@{k}"
            if p_key in query_metrics[0]:
                summary[f"mean_{p_key}"] = round(
                    sum(m.get(p_key, 0.0) for m in query_metrics) / n, 4
                )
            if r_key in query_metrics[0]:
                summary[f"mean_{r_key}"] = round(
                    sum(m.get(r_key, 0.0) for m in query_metrics) / n, 4
                )
        return summary


if __name__ == "__main__":
    print("=" * 65)
    print("Self-Test: SearchEvaluator")
    print("=" * 65)

    evaluator = SearchEvaluator()
    q_a1 = "replicating cache nodes"
    rel_a1 = evaluator.get_relevant_docs(q_a1)
    print(f"Query: '{q_a1}' -> Relevant docs: {rel_a1}")

    perf_eval = evaluator.evaluate_retrieval(q_a1, [1, 8, 3])
    print("Simulated Retrieval [Doc 1, Doc 8, Doc 3]:")
    print(f"  P@1: {perf_eval['precision@1']} | R@3: {perf_eval['recall@3']} | AP: {perf_eval['average_precision']}")
```
</details>

Run `evaluator.py`:

```bash
python3 evaluator.py
```

<p align="center">
  <img src="images/ts-08-evaluator_py.png" alt="Evaluator-Py" width="100%" />
</p>

---

### Milestone 6: Conducting the Empirical Benchmark Comparison (`benchmark.py`)

Now that both search engines and the Cranfield evaluation engine are implemented, we bring them together in a comprehensive benchmark suite: `benchmark.py`.

This master benchmarking harness executes the 6 curated queries across all four engine configurations (Custom Inverted Index, Elasticsearch Standard, Elasticsearch English, and Elasticsearch Exact), measures microsecond execution latencies using `time.perf_counter()`, evaluates quantitative IR metrics via `evaluator.py`, prints qualitative diagnostic cards, executes Lucene's `_explain` scoring breakdown, and persists results to structured JSON and text files.

Click to unfold the complete code block below, then create and save `benchmark.py` in your lab workspace:

<details>
<summary>Click to view complete benchmark.py code</summary>

```python
"""
benchmark.py - Master Shootout Runner: Homemade Inverted Index vs. Elasticsearch.

Conducts an empirical, head-to-head Information Retrieval shootout across 6 benchmark
queries contrasting text analysis, candidate retrieval mechanics, BM25 scoring,
and evaluation metrics (Precision@k, Recall@k, MAP, MRR, Latency).

Generates:
1. Console diagnostic output and side-by-side shootout tables
2. comparison_report.json (structured test results)
3. shootout_results.txt (formatted benchmark summary log)
"""

import json
import time
from pathlib import Path
from corpus import DOCUMENTS
from evaluator import SearchEvaluator
from homemade_adapter import HomemadeSearchEngine

try:
    from es_client import ElasticsearchClient
except ImportError:
    ElasticsearchClient = None

BENCHMARK_QUERIES = [
    {
        "id": "A1",
        "category": "ES Win: Morphological Stemming",
        "query": "replicating cache nodes",
        "rationale": (
            "Tests stemming: Corpus has 'replication' (Doc 1 & 8). "
            "Homemade Boolean AND fails looking for exact 'replicating'. "
            "ES English stems to 'replic', achieving high recall."
        ),
    },
    {
        "id": "A2",
        "category": "ES Win: Disjunctive Candidate Retrieval",
        "query": "redis cache clustering and sharding",
        "rationale": (
            "Tests disjunction: Doc 1 covers Redis/cache/cluster, Doc 3 covers sharding. "
            "Homemade Boolean AND fails because no single doc has all 4 terms. "
            "ES match (OR) surfaces and ranks both relevant documents."
        ),
    },
    {
        "id": "A3",
        "category": "ES Win: Stopword Filtering & IDF Weighting",
        "query": "how to achieve consensus across distributed servers",
        "rationale": (
            "Tests conversational query: 'achieve' is absent from Doc 2. "
            "Homemade AND drops to 0 hits. ES English removes stopwords and "
            "weights 'consensus' and 'servers' heavily, placing Raft at Rank 1."
        ),
    },
    {
        "id": "B1",
        "category": "Homemade Win: Hyphenated Compound Terms",
        "query": "in-memory key-value",
        "rationale": (
            "Tests compound preservation: Homemade regex keeps 'in-memory' and "
            "'key-value' intact, matching Doc 1 with 100% precision. "
            "Standard analyzer splits punctuation into 4 tokens, causing false hits."
        ),
    },
    {
        "id": "B2",
        "category": "Homemade Win: Small-Corpus IDF Separation",
        "query": "scatter-gather",
        "rationale": (
            "Tests IDF precision: Homemade unquantized float IDF yields high "
            "score separation for rare technical terms on tiny corpora, "
            "whereas Lucene quantizes norms to 1 byte."
        ),
    },
    {
        "id": "B3",
        "category": "Homemade Win: Microsecond Execution Latency",
        "query": "raft consensus leader election",
        "rationale": (
            "Tests pure CPU speed: Homemade two-pointer merge runs in RAM in "
            "under 0.05ms with 0 serialization overhead, vs 3-8ms HTTP/REST round-trip."
        ),
    },
]


def run_shootout():
    print("=" * 82)
    print("  EMPIRICAL SEARCH ENGINE SHOOTOUT: FIRST PRINCIPLES vs. ELASTICSEARCH")
    print("=" * 82)

    # 1. Initialize Evaluator
    evaluator = SearchEvaluator()
    print(f"[*] Loaded {len(evaluator.queries_map)} ground-truth judgments.")

    # 2. Initialize Homemade Engine
    print("[*] Initializing Homemade Inverted Index...")
    t0 = time.perf_counter()
    homemade_engine = HomemadeSearchEngine(DOCUMENTS)
    t_homemade_init = (time.perf_counter() - t0) * 1000
    print(
        f"    Indexed {homemade_engine.index.total_docs} docs "
        f"({homemade_engine.index.vocabulary_size()} unique terms) in {t_homemade_init:.2f}ms."
    )

    # 3. Initialize Elasticsearch
    print("[*] Connecting to Elasticsearch on localhost:9200...")
    es_available = False
    es_client = None
    if ElasticsearchClient:
        try:
            es_client = ElasticsearchClient()
            if es_client.ping():
                es_available = True
                print("    Connected successfully!")
                es_client.create_index(recreate=True)
                es_client.index_corpus(DOCUMENTS)
            else:
                print("    [WARNING] Elasticsearch ping failed (service not ready).")
        except Exception as e:
            print(f"    [WARNING] Could not connect to Elasticsearch: {e}")

    if not es_available:
        print("    --> Proceeding in STANDALONE mode (Homemade Index only).")

    # Engine configs to evaluate
    configs = [
        {"key": "homemade_bm25", "name": "Homemade (Boolean AND + BM25)"},
    ]
    if es_available:
        configs.extend(
            [
                {
                    "key": "es_standard",
                    "name": "Elasticsearch (Standard Analyzer, OR)",
                    "field": "content",
                },
                {
                    "key": "es_english",
                    "name": "Elasticsearch (English Analyzer + Stemming, OR)",
                    "field": "content.english",
                },
                {
                    "key": "es_exact",
                    "name": "Elasticsearch (Exact Hyphen Analyzer, OR)",
                    "field": "content.exact",
                },
            ]
        )

    # Storage for detailed results
    detailed_results = []
    engine_stats = {cfg["key"]: {"latencies": [], "metrics": []} for cfg in configs}

    print("\n" + "=" * 82)
    print("  EXECUTING THE 3 vs 3 BENCHMARK SUITE")
    print("=" * 82)

    output_lines = []

    for item in BENCHMARK_QUERIES:
        q_id = item["id"]
        category = item["category"]
        query_text = item["query"]
        rationale = item["rationale"]

        banner = f"\n>>> [Query {q_id}] \"{query_text}\"\n    Category:  {category}\n    Rationale: {rationale}"
        print(banner)
        output_lines.append(banner)

        query_record = {
            "query_id": q_id,
            "category": category,
            "query": query_text,
            "configs": {},
        }

        relevant_docs = evaluator.get_relevant_docs(query_text)
        rel_str = f"    Ground-Truth Relevant Docs: {sorted(list(relevant_docs))}"
        print(rel_str)
        output_lines.append(rel_str)

        for cfg in configs:
            cfg_key = cfg["key"]
            cfg_name = cfg["name"]

            # Measure search latency
            retrieved_items = []
            retrieved_ids = []

            # Warm-up / Execute
            t_start = time.perf_counter()
            if cfg_key == "homemade_bm25":
                hits = homemade_engine.search(query_text, top_k=5)
                t_elapsed_ms = (time.perf_counter() - t_start) * 1000
                retrieved_items = hits
                retrieved_ids = [h["doc_id"] for h in hits]
            else:
                field = cfg.get("field", "content")
                hits_raw = es_client.search(
                    query_text, field=field, operator="or", top_k=5
                )
                t_elapsed_ms = (time.perf_counter() - t_start) * 1000
                retrieved_items = [
                    {"doc_id": d, "score": s, "title": t} for d, s, t in hits_raw
                ]
                retrieved_ids = [d for d, s, t in hits_raw]

            # IR Evaluation
            eval_result = evaluator.evaluate_retrieval(
                query_text, retrieved_ids, k_values=[1, 3, 5, 10]
            )

            engine_stats[cfg_key]["latencies"].append(t_elapsed_ms)
            engine_stats[cfg_key]["metrics"].append(eval_result)

            query_record["configs"][cfg_key] = {
                "name": cfg_name,
                "latency_ms": round(t_elapsed_ms, 3),
                "hits_count": len(retrieved_ids),
                "retrieved": retrieved_items,
                "metrics": eval_result,
            }

            hits_summary = ", ".join(
                [f"Doc {h['doc_id']}(s={h['score']:.2f})" for h in retrieved_items[:3]]
            )
            if not hits_summary:
                hits_summary = "0 hits (Boolean AND miss)"

            res_line = (
                f"    {cfg_name:<46} | Latency: {t_elapsed_ms:6.2f}ms | "
                f"P@1: {eval_result['precision@1']:.2f} | R@3: {eval_result['recall@3']:.2f} | "
                f"AP: {eval_result['average_precision']:.2f} | Hits: [{hits_summary}]"
            )
            print(res_line)
            output_lines.append(res_line)

        detailed_results.append(query_record)

    # 4. Aggregated Summary Table
    summary_header = "\n" + "=" * 90 + "\n" + "  FINAL AGGREGATED BENCHMARK SUMMARY (6 BENCHMARK QUERIES)\n" + "=" * 90
    print(summary_header)
    output_lines.append(summary_header)

    col_fmt = "{:<48} | {:<12} | {:<8} | {:<8} | {:<8} | {:<8} | {:<8}"
    tbl_head = col_fmt.format(
        "Engine Configuration", "Mean Latency", "P@1", "P@3", "R@3", "MAP", "MRR"
    )
    separator = "-" * 90
    print(tbl_head)
    print(separator)
    output_lines.append(tbl_head)
    output_lines.append(separator)

    summary_records = []
    for cfg in configs:
        cfg_key = cfg["key"]
        cfg_name = cfg["name"]
        latencies = engine_stats[cfg_key]["latencies"]
        metrics = engine_stats[cfg_key]["metrics"]

        mean_lat = sum(latencies) / len(latencies) if latencies else 0.0
        agg = evaluator.aggregate_suite(metrics)

        row_str = col_fmt.format(
            cfg_name,
            f"{mean_lat:.2f} ms",
            f"{agg.get('mean_precision@1', 0.0):.4f}",
            f"{agg.get('mean_precision@3', 0.0):.4f}",
            f"{agg.get('mean_recall@3', 0.0):.4f}",
            f"{agg.get('mean_average_precision', 0.0):.4f}",
            f"{agg.get('mean_reciprocal_rank', 0.0):.4f}",
        )
        print(row_str)
        output_lines.append(row_str)

        summary_records.append(
            {
                "config_key": cfg_key,
                "config_name": cfg_name,
                "mean_latency_ms": round(mean_lat, 3),
                "metrics": agg,
            }
        )

    print(separator)
    output_lines.append(separator)

    # 5. BM25 Deep Dive: Scoring Explain Comparison
    explain_banner = "\n" + "=" * 90 + "\n" + "  BM25 SCORING EXPLAIN DEEP DIVE (Doc 2, 'raft consensus leader election')\n" + "=" * 90
    print(explain_banner)
    output_lines.append(explain_banner)

    # Homemade Explain for Doc 2
    hm_explain = homemade_engine.explain("raft consensus leader election", doc_id=2)
    hm_out = [
        f"--- Homemade BM25 Score: {hm_explain['bm25_score']:.4f} ---",
    ]
    for term_info in hm_explain["terms"]:
        hm_out.append(
            f"  * Term '{term_info['term']:<10}' | TF={term_info['tf']} | "
            f"IDF={term_info['idf']:.4f} | Contribution={term_info['bm25_contribution']:.4f}"
        )
    for line in hm_out:
        print(line)
        output_lines.append(line)

    if es_available:
        try:
            es_exp = es_client.explain("raft consensus leader election", doc_id=2, field="content.english")
            exp_details = es_exp.get("explanation", {})
            es_score = exp_details.get("value", 0.0)
            es_desc = exp_details.get("description", "")
            es_out = [
                f"\n--- Elasticsearch Lucene BM25 Score: {es_score:.4f} ---",
                f"  Top-level description: {es_desc[:85]}...",
            ]
            for line in es_out:
                print(line)
                output_lines.append(line)
        except Exception as e:
            print(f"  [Notice] Could not fetch ES _explain: {e}")

    # 6. Export outputs
    report_data = {
        "benchmark_queries": BENCHMARK_QUERIES,
        "summary": summary_records,
        "detailed_results": detailed_results,
    }

    out_dir = Path(__file__).parent
    json_path = out_dir / "comparison_report.json"
    txt_path = out_dir / "shootout_results.txt"

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2)
    print(f"\n[+] Exported structured JSON report to: {json_path.name}")

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines) + "\n")
    print(f"[+] Exported formatted benchmark output to: {txt_path.name}")


def run_scoring_explain():
    """Run exclusively the BM25 scoring explain deep dive between Homemade and Elasticsearch."""
    homemade_engine = HomemadeSearchEngine(DOCUMENTS)

    es_client = None
    es_available = False
    if ElasticsearchClient:
        try:
            es_client = ElasticsearchClient()
            if es_client.ping():
                es_available = True
                es_client.create_index(recreate=False)
                es_client.index_corpus(DOCUMENTS)
        except Exception:
            pass

    explain_banner = (
        "=" * 90 + "\n"
        "  BM25 SCORING EXPLAIN DEEP DIVE (Doc 2, 'raft consensus leader election')\n"
        + "=" * 90
    )
    print(explain_banner)

    hm_explain = homemade_engine.explain("raft consensus leader election", doc_id=2)
    print(f"--- Homemade BM25 Score: {hm_explain['bm25_score']:.4f} ---")
    for term_info in hm_explain["terms"]:
        print(
            f"  * Term '{term_info['term']:<10}' | TF={term_info['tf']} | "
            f"IDF={term_info['idf']:.4f} | Contribution={term_info['bm25_contribution']:.4f}"
        )

    if es_available:
        try:
            es_exp = es_client.explain("raft consensus leader election", doc_id=2, field="content.english")
            exp_details = es_exp.get("explanation", {})
            es_score = exp_details.get("value", 0.0)
            es_desc = exp_details.get("description", "")
            print(f"\n--- Elasticsearch Lucene BM25 Score: {es_score:.4f} ---")
            print(f"  Top-level description: {es_desc[:85]}...")
        except Exception as e:
            print(f"  [Notice] Could not fetch ES _explain: {e}")
    else:
        print("\n[Notice] Elasticsearch not reachable on localhost:9200. Only Homemade Index breakdown displayed.")


if __name__ == "__main__":
    import sys
    if "--explain" in sys.argv or "--explain-only" in sys.argv:
        run_scoring_explain()
    else:
        run_shootout()
```
</details>

---

## Stage 5: Verification, Validation & Benchmark Deep-Dive

---

### Step 5.1: Executing the Benchmark Suite

Run the master benchmark suite in your terminal:

```bash
python3 benchmark.py
```

<p align="center">
  <img src="images/ts-benchmark-01.png" alt="Benchmark-01" width="100%" />
</p>
<p align="center">
  <img src="images/ts-benchmark-02.png" alt="Benchmark-02" width="100%" />
</p>
<p align="center">
  <img src="images/ts-benchmark-03.png" alt="Benchmark-03" width="100%" />
</p>

*(Note: Exact values for query latencies reflect real-world execution on the Poridhi Cloud VM. In-memory Python RAM execution averages 0.05ms, while containerized Elasticsearch REST operations average between 6.62ms and 8.12ms due to network I/O, JSON serialization, and Lucene segment reader coordination. The 100% Precision@1 ranking dominance of Elasticsearch's English Analyzer remains decisive across all runs)*.

---

### Step 5.2: The 3 vs 3 Decision Matrix & Root Causes

<p align="center">
  <img src="images/diagram-5-qualitative-3vs3-shootout-matrix.svg" alt="The 3 vs 3 Shootout Qualitative Decision Matrix" width="100%" />
</p>

---

### Step 5.3: The 3 Comprehensive Comparison Tables

#### Table 1: High-Level Architectural Trade-Offs

| System Dimension | Custom Homemade Inverted Index | Production Elasticsearch (Apache Lucene) |
| :--- | :--- | :--- |
| **Storage Architecture** | Single in-memory Python dictionary | Multi-format segments on disk: Inverted Index, Doc Values, Stored Fields |
| **Ingestion Pipeline** | In-process synchronous memory append | `DocumentsWriterPerThread` RAM buffer + on-disk Translog + NRT Refresh |
| **Query Latency** | **0.02ms - 0.11ms** (Pure RAM CPU cache execution) | **5.0ms - 13.3ms** (HTTP REST, Netty threads, Lucene segments, JSON) |
| **Candidate Retrieval** | Two-pointer sorted posting intersection (Strict AND) | Disjunctive union (Boolean OR) with Weak AND (WAND) top-K pruning |
| **Text Analysis** | Single regex tokenizer (`\b[a-zA-Z0-9_-]+\b`) | Multi-Field architecture (`standard`, `english` stemmer, custom regex) |
| **BM25 Length Norm** | Exact 64-bit IEEE 754 float dynamic calculation | Table-quantized 1-byte lossy float (`computeNorm`) |
| **GC & Memory Safety** | Python heap reference counting; no JVM overhead | Doc Values memory-mapped via OS Page Cache (`mmap`) bypassing JVM GC |
| **Horizontal Scalability**| Single process, single machine only | Distributed primary/replica shards, automated partition routing |

#### Table 2: The 3 vs 3 Benchmark Quantitative Comparison

| Query ID & Query Text | Focus Architecture Category | Homemade P@1 / R@3 / AP | Homemade Latency | ES English P@1 / R@3 / AP | ES English Latency | Decisive Architectural Winner |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **A1**: `replicating cache nodes` | Morphological Stemming | 0.00 / 0.00 / 0.00 | **0.06 ms** | **1.00 / 0.67 / 0.92** | 7.33 ms | **Elasticsearch** (Porter stemmer matches `replication`) |
| **A2**: `redis cache clustering and sharding` | Disjunctive Matching | 0.00 / 0.00 / 0.00 | **0.02 ms** | **1.00 / 0.67 / 0.92** | 7.25 ms | **Elasticsearch** (OR union scores Doc 1 and Doc 3) |
| **A3**: `how to achieve consensus across servers` | Stopwords & IDF | 0.00 / 0.00 / 0.00 | **0.02 ms** | **1.00 / 0.50 / 0.75** | 8.49 ms | **Elasticsearch** (Stopwords stripped, Doc 2 at #1) |
| **B1**: `in-memory key-value` | Hyphen Compound Terms | **1.00 / 1.00 / 1.00** | **0.11 ms** | 1.00 / 1.00 / 1.00 | 5.94 ms | **Homemade Index** (0 false hits; ES diluted without exact) |
| **B2**: `scatter-gather` | Small-Corpus IDF Precision | **1.00 / 1.00 / 1.00** | **0.04 ms** | 1.00 / 1.00 / 1.00 | 4.99 ms | **Homemade Index** (Unquantized float IDF separation) |
| **B3**: `raft consensus leader election` | Microsecond CPU Speed | **1.00 / 0.50 / 0.50** | **0.05 ms** | 1.00 / 0.50 / 0.50 | 5.73 ms | **Homemade Index** (115x faster execution for identical top-1) |

#### Table 3: Summary Macro Metrics (Aggregated Across 6 Benchmark Queries)

| Engine Configuration | Mean Latency | Mean Precision@1 | Mean Precision@3 | Mean Recall@3 | Mean Average Precision (MAP) | Mean Reciprocal Rank (MRR) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Homemade (Boolean AND + BM25)** | **0.05 ms** | 0.5000 | 0.1666 | 0.4167 | 0.4167 | 0.5000 |
| **Elasticsearch (Standard Analyzer, OR)** | 8.12 ms | 0.8333 | 0.5000 | **0.8056** | 0.7731 | 0.9167 |
| **Elasticsearch (English Analyzer + Stem, OR)**| 6.62 ms | **1.0000** | 0.4444 | 0.7222 | **0.8472** | **1.0000** |
| **Elasticsearch (Exact Hyphen Analyzer, OR)** | 6.67 ms | 0.8333 | 0.5000 | **0.8056** | 0.7731 | 0.9167 |

---

### Step 5.4: Architectural Analysis: Deconstructing the Results

Let us step back and analyze what these benchmark numbers actually reveal about systems architecture.

#### 1. The Latency Paradox: Why 0.05ms vs. 6.62ms is Not a Simple Python Victory
At first glance, a junior engineer might look at the latency column and conclude: *"Our custom Python index runs in 0.05 milliseconds on average in the Poridhi Cloud VM, while Elasticsearch takes 6.62 to 8.12 milliseconds. Our Python script is over 130 to 160 times faster!"*

That is a dangerous architectural misinterpretation.
* Your Python index runs inside the same operating system process as the benchmark script. Traversing the posting list is a tight, uninterrupted loop scanning contiguous RAM already loaded into the CPU's L1/L2 hardware caches. It performs zero network I/O, zero JSON parsing, zero thread context switching, and zero disk durability verification.
* Elasticsearch, by contrast, is paying the **Distributed Network & Abstraction Tax**:
  1. The benchmark script serializes the query into an HTTP POST payload.
  2. The packet travels over the TCP network stack to port 9200.
  3. Netty threads in the JVM accept the connection, parse the JSON, and validate security.
  4. The search phase coordinates across cluster threads, opens segment readers, evaluates WAND thresholds, and inspects `.del` bitsets.
  5. Matching document payloads are hydrated from LZ4-compressed `.fdt` stored field blocks on disk.
  6. The response is serialized back into JSON and transmitted over HTTP.

Elasticsearch takes 6 to 8 milliseconds in the containerized cloud VM not because Lucene is slow, but because it provides horizontal sharding, crash-safe durability, dynamic schema mapping, and cluster resilience. When your dataset grows to 10 terabytes, the Python script crashes with an out-of-memory error on line 1, whereas Elasticsearch seamlessly routes queries across a 50-node cluster.

#### 2. The Conjunctive Cliff: The Silent Killer of Exploratory Search
Look at Query A2: `"redis cache clustering and sharding"`.
* Document 1 is an authoritative guide on Redis in-memory caching and clustering.
* Document 3 is an authoritative guide on database sharding and partitioning.

Both documents are profoundly valuable to a developer researching distributed caching and data partitioning. Yet our custom Python index returned **0 hits** (Recall@3 = 0.0000, Precision@1 = 0.0000).

Why? Because our custom index enforced **Conjunctive Retrieval (Boolean AND)**. A two-pointer intersection requires a candidate document to appear in *every single* posting list. Because Document 1 did not contain the token `sharding`, and Document 3 did not contain `redis`, the intersection was the empty set.

Elasticsearch's Disjunctive Match query (`operator: "or"`) evaluated the union of posting lists, accumulating BM25 scores across matching terms. It ranked Document 1 at Rank 1 (Score: 5.77) and Document 3 at Rank 2 (Score: 2.83), delivering an Average Precision of 0.9167.

#### 3. The Linguistic Normalization Dividend
In Query A1 (`"replicating cache nodes"`), our custom index again returned 0 hits because the corpus vocabulary only contained `"replication"`. 

Elasticsearch's English Analyzer applied the algorithmic **Porter Stemmer**, stripping suffixes to normalize both `"replicating"` and `"replication"` to the common stem `"replic"`. This single linguistic transformation elevated Elasticsearch's performance to **100% Precision@1** across all 6 benchmark queries and achieved an overall **Mean Average Precision (MAP) of 0.8472**.

#### 4. The Value of Domain-Specific Tokenization
In Query B1 (`"in-memory key-value"`), the tables turned.
* Our custom regex tokenizer (`\b[a-zA-Z0-9_-]+\b`) recognized hyphens as constituent word characters, indexing `"in-memory"` and `"key-value"` as single, atomic compound tokens. It matched Document 1 with 100% precision and zero false positives.
* Elasticsearch's Standard Analyzer stripped punctuation according to generic Unicode word boundary rules (UAX #29), splitting the phrase into four independent words: `"in"`, `"memory"`, `"key"`, and `"value"`. The query matched Document 3 (which mentioned `"partition key"`) and Document 5 (which mentioned `"data keys"`), diluting precision.
* **The Takeaway**: Generic tokenizers work well for prose, but specialized technical domains (code search, chemistry, medical databases) require custom pattern analyzers.

---

### Step 5.5: Scoring Explain Deep Dive

To understand how Lucene arrives at its BM25 score compared to our Python implementation, examine the mathematical breakdown for Benchmark Query B3 (`raft consensus leader election`, evaluating Document 2).

#### How to Run the Scoring Explain Benchmark

You can run and inspect this scoring comparison in three ways:

1. **Automatic Execution with the Full Benchmark**: When you run `python3 benchmark.py` in Step 5.1, the benchmark runner executes all 6 test queries, displays the summary table, and automatically concludes with this exact BM25 scoring deep dive. The output is also archived to `shootout_results.txt`.
2. **Dedicated Scoring Command**: If you want to run *only* this scoring explain comparison without re-running the entire 6-query test suite, pass the `--explain` flag:
   ```bash
   python3 benchmark.py --explain
   ```
3. **Inspect the Saved Benchmark Log**: To view the results from your previous run directly from the archived log:
   ```bash
   grep -A 20 "BM25 SCORING EXPLAIN" shootout_results.txt
   ```

<p align="center">
  <img src="images/ts-benchmark-explain.png" alt="Benchmark-Explain" width="100%" />
</p>

#### Why Do the Total Scores Differ? (11.4203 vs 9.1549)
Both engines implement the Okapi BM25 ranking function, yet our custom Python implementation produces a total score of **11.4203** while Elasticsearch Lucene produces **9.1549**. Why does this difference occur?

1. **Document Length Normalization Quantization**:
   In Document 2, the document length is `doc_len = 29` words, compared against the corpus average length `avgdl = 36.44`.
   * **Homemade Engine**: Evaluates `len_norm = 1.0 - 0.75 + 0.75 * (29 / 36.44) = 0.8468` using standard 64-bit IEEE 754 floating-point precision. This exact norm scales down the term saturation denominator, yielding a higher term frequency multiplier (`TF_Norm = 1.4588` for TF=2 and `1.6434` for TF=3).
   * **Elasticsearch / Lucene**: Storing an 8-byte floating-point number for document length across millions of documents consumes excessive disk and memory. Lucene compresses document length normalization into a single 1-byte lossy float table (`computeNorm` using 3 bits of mantissa and 5 bits of exponent). On miniature corpora, this table quantization introduces a slight compression difference, resulting in a conservative norm scaling factor.

2. **Stemming & Multi-Field Term Frequency Aggregation**:
   * **Homemade Engine**: Performs strict token matching for `"election"` (`TF=2`). It only counts exact string matches for the token.
   * **Elasticsearch (English Analyzer)**: Applies the algorithmic Porter Stemmer, reducing `"election"` to the root stem `"elect"`. The query matches the term across both the document `title` and `content` fields, aggregating term frequencies across the analyzed multi-field representation.

3. **IDF Smoothing & Boundary Adjustments**:
   * **Homemade Engine**: Implements the classic Robertson-Spärck Jones IDF formula: `IDF(t) = ln(1 + (N - DF + 0.5) / (DF + 0.5))`. With `N=9` and `DF=1`, `IDF = ln(1 + 8.5 / 1.5) = ln(6.6667) = 1.8971`.
   * **Elasticsearch / Lucene**: Lucene uses an adapted version with internal smoothing constants and minimum boundary checks to ensure that rare terms in very small or single-shard indices do not produce volatile score swings, yielding an effective term weight of 2.1583 per term and a total score of 9.1549.

---

## Stage 6: Real-World Applications & System Design Scenarios

---

### Scenario 1: High-Throughput E-Commerce Product Catalog
* **Context**: An online retailer with 10 million SKUs, 50,000 queries per second, and continuous price updates.
* **Architecture Decision**: **Elasticsearch with Multi-Fields and Doc Values**.
* **Rationale**: Multi-fields enable simultaneous partial keyword matching on brand names and Porter-stemmed search on product descriptions. Price and inventory filters leverage columnar **Doc Values** mapped into OS Page Cache, enabling microsecond facet generation without burdening the JVM heap.

---

### Scenario 2: In-Process Microsecond Code Search & IDE Autocomplete
* **Context**: A desktop code editor indexing an open workspace repository of 5,000 source files.
* **Architecture Decision**: **Embedded Custom Inverted Index (First Principles)**.
* **Rationale**: Running a Docker container or Java runtime consumes 1.5GB of RAM and introduces 3ms of IPC/REST overhead per keystroke. An in-memory inverted index written directly in the host language (Python, Rust, or C++) executes candidate intersection in **0.02ms** directly in CPU L1/L2 cache with zero serialization overhead and zero memory footprint.

---

### Scenario 3: Legal & Regulatory Discovery (Strict Exact Matching)
* **Context**: An e-discovery compliance system searching 50 million email records for subpoena evidence.
* **Architecture Decision**: **Elasticsearch with Custom Exact Tokenizers (No Stemmer)**.
* **Rationale**: In legal discovery, morphological stemming is dangerous: searching for `contracting` must never match `contraction` or `contractor`. Engineers configure a custom pattern analyzer that disables stemming and stopword stripping, ensuring 100% precision auditability.

---

### Scenario 4: Large-Scale Observability & Log Analytics (Elasticsearch / OpenSearch at Scale)
* **Context**: Ingesting 500,000 log events per second from Kubernetes microservices clusters.
* **Architecture Decision**: **Time-Series Data Streams with Relaxed Refresh Intervals (30s)**.
* **Rationale**: High ingestion volume requires minimizing segment write amplification. By relaxing the NRT refresh interval from 1 second to 30 seconds (`index.refresh_interval: 30s`), Lucene buffers larger batches in RAM, reducing small segment merges by over 80% and avoiding I/O starvation on physical disks.

---

## Stage 7: Production Troubleshooting & FAQ

---

### FAQ 1: Why did my newly indexed documents disappear when searched immediately?
* **Root Cause**: The **Near-Real-Time (NRT) Refresh Gate**. Elasticsearch buffers incoming documents in memory (`DocumentsWriterPerThread`). Documents are completely invisible to `IndexSearcher` until a refresh flushes the buffer into an OS page-cache segment.
* **Solution**: In automated integration test suites, always call explicit refresh immediately after bulk indexing:
  ```python
  client.indices.refresh(index="distributed_systems_corpus")
  ```
  In production, rely on the automated background 1-second refresh interval to preserve write throughput.

---

### FAQ 2: Why does Elasticsearch take 3ms while our Python index runs in 0.02ms?
* **Root Cause**: The **Distributed Network & Abstraction Tax**.
  * Your Python index runs in the same memory space as the interpreter. Posting traversal is a tight C-speed loop reading Python lists already cached in CPU L1/L2 cache.
  * Elasticsearch dispatches the query over an HTTP TCP connection, parses JSON in Netty threads, routes through cluster transport layers, executes Lucene segment readers, checks `.del` bitmaps, serializes hits to JSON, and transfers bytes back over HTTP.
* **Rule of Thumb**: Elasticsearch is engineered for scale, durability, and multi-node sharding; embedded indexes are engineered for microsecond in-process speed.

---

### FAQ 3: How does Elasticsearch avoid OutOfMemory (OOM) errors during large aggregations?
* **Root Cause**: In older versions of search engines, aggregations relied on **Fielddata**, which inverted postings directly into JVM heap memory. Under heavy traffic, large cardinality fields quickly exhausted JVM heap, triggering catastrophic OOM crashes.
* **Solution**: Modern Elasticsearch uses on-disk **Doc Values** (`.dvd` files) by default for all `keyword`, `date`, `numeric`, and `boolean` fields. Doc Values are mapped directly into the Linux kernel's **OS Page Cache** using `mmap`. The operating system dynamically swaps pages in and out of RAM without touching the JVM garbage collector.

---

### FAQ 4: Why does Lucene store document length norms in a single byte?
* **Root Cause**: Storing document length as an 8-byte integer for every field across 100 million documents requires 800MB of RAM per field just to hold lengths in memory.
* **Solution**: Lucene table-quantizes document length normalization into a single 1-byte float with 3 bits of mantissa and 5 bits of exponent. While this introduces minor rounding errors on tiny documents, it slashes memory consumption by 87.5% with zero measurable impact on ranking quality at scale.

---

### FAQ 5: What is WAND and how does it speed up multi-term queries?
* **Root Cause**: In disjunctive queries (`operator: "or"`), the candidate pool is the union of all matching posting lists. For common terms, this union contains millions of candidates.
* **Solution**: **Weak AND (WAND)** tracks the maximum possible score contribution (`max_score`) of each term. It sorts active posting iterators and skips past documents whose theoretical score ceiling cannot exceed the lowest score in the current top-K priority queue, reducing evaluated documents by up to 90%.

---

### FAQ 6: When should an engineer choose an embedded inverted index over Elasticsearch?
* Choose an **embedded in-process inverted index** when:
  1. The corpus fits entirely in RAM (under 500,000 documents).
  2. Sub-millisecond (under 0.1ms) latency is a hard SLA.
  3. Zero operational infrastructure (no Docker, no Java runtime, no network ports) is mandatory.
* Choose **Elasticsearch** when:
  1. Data volume exceeds single-machine RAM (requires horizontal sharding).
  2. Aggregations, facets, and geo-spatial queries are required.
  3. Automated replica failover, cluster self-healing, and durability are non-negotiable.

---

## Stage 9: Conclusion

### Codebase File Inventory

| File | Primary Class / Entrypoint | Key Responsibility |
| :--- | :--- | :--- |
| `docker-compose.yml` | Container Service | Single-node Elasticsearch 8.11.0 cluster definition (`localhost:9200`) with disabled security for local benchmarking. |
| `test_es.py` | Health Verification Script | 4-step HTTP connectivity and health check verifying cluster readiness. |
| `es_client.py` | `ElasticsearchBenchmarkClient` | Multi-field mappings (`standard`, `english`, `exact`), bulk indexing, and Lucene search/explain execution. |
| `homemade_adapter.py` | `HomemadeSearchEngine` | Adapter unifying the first-principles Inverted Index, TextAnalyzer, and SearchRanker into a standardized benchmark interface. |
| `generate_judgments.py` | Ground-Truth Generator | Standalone script annotating binary relevance for the 6 benchmark queries based on Cranfield evaluation principles. |
| `evaluator.py` | `SearchEvaluator` | Information retrieval evaluation engine calculating Precision@k, Recall@k, Reciprocal Rank, Average Precision, MAP, and MRR. |
| `benchmark.py` | Master Benchmark Suite | Dual-engine comparison harness measuring microsecond latencies, evaluating IR metrics, and executing BM25 scoring explain deep dives. |

### System Design Competencies Mastered

* [x] **The In-Memory Scalability Wall**: Understanding why single-threaded in-memory Python dictionaries fail in production due to interpreter heap GC pressure, lack of durability, and inability to scale horizontally.
* [x] **Lucene Segment Architecture**: Understanding immutable on-disk segment storage, FST term dictionaries (`.tim`), SIMD PFOR-DELTA posting lists (`.doc`), and columnar Doc Values (`.dvd`) memory-mapped via the OS page cache (`mmap`).
* [x] **Near-Real-Time (NRT) Ingestion & Durability**: Distinguishing between 1-second NRT `refresh` (making data searchable in OS page cache) vs. periodic `flush` with `fsync` (persisting segments to physical disk and truncating the Translog WAL).
* [x] **Multi-Field Linguistic Strategy**: Overcoming morphological blindness by indexing text simultaneously across standard Unicode tokenizers, language-specific Porter stemmers (`content.english`), and exact hyphen regex tokenizers (`content.exact`).
* [x] **Candidate Retrieval Dynamics (Conjunctive Cliff vs. Disjunctive WAND)**: Analyzing why strict Boolean AND fails on multi-term and conversational queries, and how Lucene's Disjunctive OR with Block-Max WAND dynamic score pruning delivers 100% Precision@1 without evaluating millions of non-competitive candidates.
* [x] **The Distributed Network & Abstraction Tax**: Explaining the latency paradox—why an in-memory Python index executes in 0.05ms in CPU cache while Elasticsearch takes 6–8ms over HTTP/Netty to provide clustering, replication, and durability.
* [x] **Empirical Information Retrieval Evaluation (Cranfield Paradigm)**: Mathematically computing and interpreting standard IR metrics (Precision@k, Recall@k, Reciprocal Rank, Average Precision, MAP, and MRR) to objectively benchmark search engine quality against ground-truth judgments.

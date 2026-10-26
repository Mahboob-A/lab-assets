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

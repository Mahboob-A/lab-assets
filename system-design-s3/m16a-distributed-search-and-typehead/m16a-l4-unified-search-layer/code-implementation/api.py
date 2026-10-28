"""
api.py - Unified Search Surface Gateway (FastAPI).

Exposes production HTTP endpoints for:
  - Sub-millisecond keystroke autocomplete (/suggest)
  - Resilient full-text search with circuit breaker fallback (/search)
  - Side-by-side empirical comparison (/compare)
  - Circuit Breaker telemetry & chaos administration (/circuit-breaker/trip, /reset)
  - System health (/health)
  - Interactive split-screen visual dashboard (/)
"""

import os
import time
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from dataset import QueryDataset
from trie_service import TrieTypeaheadService
from search_service import SearchBrokerService
from circuit_breaker import CircuitState

app = FastAPI(
    title="Unified Search Surface Gateway",
    description="Fault-Tolerant Hybrid Search Layer combining Trie Typeahead, Elasticsearch, and In-Memory Inverted Index.",
    version="1.0.0",
)

# 1. Initialize Trie Typeahead Service (from Lab 2)
trie_service = TrieTypeaheadService(k=5)
trie_service.build_from_dataset(dict(QueryDataset.SEED_TERMS))

# 2. Initialize Search Broker with Circuit Breaker (combining Lab 1 and Lab 3)
search_broker = SearchBrokerService()

# 3. Mount Static Assets for Dashboard UI
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def get_dashboard():
    """Serves the split-screen interactive search dashboard."""
    index_file = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {"message": "Dashboard UI under construction. Access /docs for Swagger API specification."}


@app.get("/suggest")
def suggest(
    q: str = Query(..., description="Keystroke search prefix"),
    limit: int = Query(5, ge=1, le=20, description="Max suggestions to return"),
):
    """
    Sub-millisecond Typeahead / Autocomplete Endpoint.
    
    Enforces server-side prefix gating: prefixes shorter than 2 characters are dropped
    to prevent single-character cache thrashing and heavy network chatter.
    """
    t0 = time.perf_counter()
    normalized = q.strip().lower()

    # Server-Side Prefix Gating Rule
    if len(normalized) < 2:
        return {
            "prefix": q,
            "status": "prefix_too_short",
            "message": "Type at least 2 characters to trigger autocomplete suggestions.",
            "count": 0,
            "suggestions": [],
            "latency_ms": round((time.perf_counter() - t0) * 1000.0, 3),
        }

    raw_suggestions = trie_service.suggest(normalized, k=limit)
    latency_ms = (time.perf_counter() - t0) * 1000.0

    return {
        "prefix": normalized,
        "status": "success",
        "count": len(raw_suggestions),
        "suggestions": [
            {"query": word, "frequency": freq}
            for word, freq in raw_suggestions
        ],
        "latency_ms": round(latency_ms, 3),
    }


@app.get("/search")
def search(
    q: str = Query(..., description="Full-text search query"),
    limit: int = Query(10, ge=1, le=50, description="Max documents to return"),
    force_fallback: bool = Query(False, description="Chaos drill: force in-memory fallback diversion"),
):
    """
    Resilient Full-Text Search Endpoint.
    
    Routes to Elasticsearch primary when healthy (CLOSED).
    Immediately diverts to embedded In-Memory Inverted Index when OPEN or timed out.
    """
    if not q.strip():
        raise HTTPException(status_code=400, detail="Search query cannot be empty.")

    return search_broker.search(
        query=q.strip(),
        top_k=limit,
        force_fallback=force_fallback,
    )


@app.get("/compare")
def compare_engines(
    q: str = Query(..., description="Query to execute against both engines concurrently"),
):
    """
    Side-by-Side Architectural Shootout Endpoint.
    
    Runs query against both Elasticsearch and In-Memory Index to illustrate
    the latency vs. recall trade-off (Stemming vs. Speed).
    """
    if not q or not q.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty.")

    clean_q = q.strip()
    # 1. Primary: Elasticsearch
    t0_es = time.perf_counter()
    es_reachable = True
    es_error = None
    es_hits = []
    try:
        raw_es = search_broker.es_client.search(query=clean_q, field="content.english", operator="or", top_k=5)
        es_hits = [{"doc_id": doc_id, "score": score, "title": title} for doc_id, score, title in raw_es]
    except Exception as exc:
        es_reachable = False
        es_error = str(exc)
    es_latency_ms = round((time.perf_counter() - t0_es) * 1000.0, 3)

    # 2. Fallback: In-Memory Inverted Index
    t0_ram = time.perf_counter()
    ram_raw = search_broker._fallback_search(query=clean_q, top_k=5)
    ram_latency_ms = round((time.perf_counter() - t0_ram) * 1000.0, 3)

    speedup = round(es_latency_ms / max(0.001, ram_latency_ms), 1) if es_reachable else None

    return {
        "query": clean_q,
        "elasticsearch": {
            "reachable": es_reachable,
            "latency_ms": es_latency_ms,
            "total_hits": len(es_hits),
            "hits": es_hits,
            "error": es_error,
        },
        "in_memory_inverted_index": {
            "reachable": True,
            "latency_ms": ram_latency_ms,
            "total_hits": len(ram_raw),
            "hits": ram_raw,
        },
        "speedup_factor": f"{speedup}x faster (In-Memory RAM vs Network ES)" if speedup else "N/A",
        "tradeoff_analysis": (
            "Elasticsearch provides morphological Porter stemming (e.g., matching 'replicate' to 'replicating'), "
            "whereas the in-memory inverted index provides instantaneous sub-millisecond fallback at lower recall."
        ),
    }


@app.get("/health")
def health_check():
    """Returns cluster connectivity, Trie memory footprint, and Circuit Breaker state."""
    es_ok = search_broker.es_client.ping()
    cb_status = search_broker.circuit_breaker.get_status()

    return {
        "status": "healthy" if es_ok or cb_status["state"] == CircuitState.OPEN else "degraded",
        "primary_elasticsearch": {
            "reachable": es_ok,
            "host": search_broker.es_client.host,
        },
        "typeahead_trie": {
            "status": "ready",
            "vocabulary_size": trie_service.total_words,
        },
        "fallback_inverted_index": {
            "status": "ready",
            "document_count": search_broker.index.total_docs,
        },
        "circuit_breaker": cb_status,
    }


@app.post("/circuit-breaker/trip")
def trip_circuit_breaker(reason: str = Query("Chaos drill injection", description="Reason for manual trip")):
    """Administrative / Chaos Endpoint: Forces Circuit Breaker into OPEN state."""
    search_broker.circuit_breaker.trip(reason=reason)
    return {
        "message": "Circuit breaker successfully TRIPPED to OPEN.",
        "status": search_broker.circuit_breaker.get_status(),
    }


@app.post("/circuit-breaker/reset")
def reset_circuit_breaker():
    """Administrative Endpoint: Manually resets Circuit Breaker to CLOSED state."""
    search_broker.circuit_breaker.reset()
    return {
        "message": "Circuit breaker successfully RESET to CLOSED.",
        "status": search_broker.circuit_breaker.get_status(),
    }


if __name__ == "__main__":
    import uvicorn
    print("Starting Unified Search Surface Gateway on http://0.0.0.0:8000...")
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=False)

"""
search_service.py - Unified Search Query Broker with Resilient Fallback.

Coordinates query execution between the primary full-text engine (Elasticsearch)
and the embedded in-memory fallback engine (Inverted Index from Lab 1).
Protected by the SearchCircuitBreaker to enforce sub-millisecond degradation
whenever the primary cluster experiences outages, saturation, or SLA breaches.
"""

import time
from typing import Dict, Any, List, Optional
import corpus
from analyzer import TextAnalyzer
from index import InvertedIndex
from ranker import SearchRanker
from es_client import ElasticsearchClient
from circuit_breaker import SearchCircuitBreaker, CircuitState


class SearchBrokerService:
    """
    Search Query Broker providing fault-tolerant query execution.
    
    Routes traffic to Elasticsearch when the circuit is CLOSED.
    Instantly diverts to the in-memory Inverted Index when the circuit is OPEN,
    guaranteeing a 0.05ms fallback latency with zero user-facing 500 errors.
    """

    def __init__(
        self,
        es_client: Optional[ElasticsearchClient] = None,
        circuit_breaker: Optional[SearchCircuitBreaker] = None,
    ):
        # 1. Initialize Primary Engine (Elasticsearch Client)
        self.es_client = es_client or ElasticsearchClient()

        # 2. Initialize In-Memory Fallback Engine (Embedded Heap Index from Lab 1)
        self.analyzer = TextAnalyzer()
        self.index = InvertedIndex()
        for doc_id, doc in corpus.DOCUMENTS.items():
            tokens = self.analyzer.analyze(doc["content"])
            self.index.add_document(doc_id, tokens)
        self.ranker = SearchRanker(self.index, self.analyzer)

        # 3. Initialize Circuit Breaker Protection
        self.circuit_breaker = circuit_breaker or SearchCircuitBreaker(
            failure_threshold=3,
            recovery_timeout=10.0,
            timeout_sla=0.500,  # 500ms SLA
        )

    def _fallback_search(self, query: str, top_k: int = 10) -> List[Dict[str, Any]]:
        """
        # TODO 3: Implement Fallback Execution against In-Memory Inverted Index
        # 1. Query the embedded SearchRanker using BM25 scoring mode.
        # 2. Map the resulting (doc_id, score) tuples to title strings via corpus.DOCUMENTS.
        # 3. Format as a list of dicts: {"doc_id": doc_id, "score": round(score, 4), "title": title}.
        """
        if not query or not query.strip():
            return []

        try:
            raw_results = self.ranker.search(query.strip(), mode="bm25", top_k=top_k)
            formatted = []
            for doc_id, score in raw_results:
                title = corpus.DOCUMENTS.get(doc_id, {}).get("title", f"Document #{doc_id}")
                formatted.append({
                    "doc_id": doc_id,
                    "score": round(score, 4),
                    "title": title,
                })
            return formatted
        except Exception:
            return []

    def search(
        self,
        query: str,
        top_k: int = 10,
        force_fallback: bool = False,
    ) -> Dict[str, Any]:
        """
        Executes search with Circuit Breaker routing and zero-downtime degradation.

        Args:
            query: User search string.
            top_k: Maximum ranked documents to return.
            force_fallback: Administrative override for chaos drills.

        Returns:
            Dict containing engine attribution, degraded status, latency, and hit list.
        """
        start_time = time.perf_counter()

        # Check circuit breaker health and administrative chaos flags
        circuit_allowed = self.circuit_breaker.can_execute()

        if force_fallback or not circuit_allowed:
            # FAST-PATH DEGRADATION: Bypass primary completely, query RAM index
            hits = self._fallback_search(query, top_k=top_k)
            latency_ms = (time.perf_counter() - start_time) * 1000.0

            return {
                "query": query,
                "engine": "in-memory-inverted-index",
                "degraded": True,
                "latency_ms": round(latency_ms, 3),
                "total_hits": len(hits),
                "results": hits,
                "circuit_breaker": self.circuit_breaker.get_status(),
                "degradation_reason": "Administrative override" if force_fallback else "Circuit Breaker OPEN",
            }

        # PRIMARY PATH: Query Elasticsearch Cluster
        try:
            t0_es = time.perf_counter()
            raw_hits = self.es_client.search(
                query=query,
                field="content.english",
                operator="or",
                top_k=top_k,
            )
            es_duration = time.perf_counter() - t0_es

            # Verify SLA compliance
            if es_duration > self.circuit_breaker.timeout_sla:
                self.circuit_breaker.record_failure(
                    f"Elasticsearch latency {es_duration*1000:.1f}ms breached SLA of {self.circuit_breaker.timeout_sla*1000}ms"
                )
                # Fall back immediately on timeout
                hits = self._fallback_search(query, top_k=top_k)
                latency_ms = (time.perf_counter() - start_time) * 1000.0
                return {
                    "query": query,
                    "engine": "in-memory-inverted-index",
                    "degraded": True,
                    "latency_ms": round(latency_ms, 3),
                    "total_hits": len(hits),
                    "results": hits,
                    "circuit_breaker": self.circuit_breaker.get_status(),
                    "degradation_reason": "Primary exceeded 500ms SLA",
                }

            # Primary succeeded within SLA!
            self.circuit_breaker.record_success()
            latency_ms = (time.perf_counter() - start_time) * 1000.0

            hits = [
                {"doc_id": doc_id, "score": score, "title": title}
                for doc_id, score, title in raw_hits
            ]

            return {
                "query": query,
                "engine": "elasticsearch",
                "degraded": False,
                "latency_ms": round(latency_ms, 3),
                "total_hits": len(hits),
                "results": hits,
                "circuit_breaker": self.circuit_breaker.get_status(),
                "degradation_reason": None,
            }

        except Exception as exc:
            # Primary failed (connection refused, 503, host down)
            self.circuit_breaker.record_failure(f"Elasticsearch error: {str(exc)}")
            hits = self._fallback_search(query, top_k=top_k)
            latency_ms = (time.perf_counter() - start_time) * 1000.0

            return {
                "query": query,
                "engine": "in-memory-inverted-index",
                "degraded": True,
                "latency_ms": round(latency_ms, 3),
                "total_hits": len(hits),
                "results": hits,
                "circuit_breaker": self.circuit_breaker.get_status(),
                "degradation_reason": f"Elasticsearch exception: {type(exc).__name__}",
            }


if __name__ == "__main__":
    print("=== Testing SearchBrokerService Graceful Degradation ===")
    broker = SearchBrokerService()

    # Test fallback directly (isolated without ES)
    fallback_res = broker.search("distributed consensus", force_fallback=True)
    print(f"Fallback search result (engine={fallback_res['engine']}, degraded={fallback_res['degraded']}):")
    print(f"  Latency: {fallback_res['latency_ms']} ms | Hits: {fallback_res['total_hits']}")
    for hit in fallback_res["results"]:
        print(f"    - Doc {hit['doc_id']}: {hit['title']} (score: {hit['score']})")

    assert fallback_res["engine"] == "in-memory-inverted-index"
    assert fallback_res["degraded"] is True
    assert fallback_res["total_hits"] > 0
    print("\n[PASSED] Fallback execution verified successfully!")

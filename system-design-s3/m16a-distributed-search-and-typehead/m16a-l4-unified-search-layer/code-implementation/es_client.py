"""
es_client.py - Elasticsearch Client & Multi-Field Mapping Engine.

Manages connection to Elasticsearch, defines explicit multi-field index schemas,
executes bulk document ingestion with the mandatory near-real-time (NRT)
refresh gate, and executes full-text search and scoring explanations.
"""

import time
from typing import Any
import corpus
from elasticsearch import Elasticsearch, helpers

INDEX_NAME = "distributed_systems_corpus"


class ElasticsearchClient:
    """
    Elasticsearch client wrapper providing index management, multi-field analysis,
    bulk indexing, and search capabilities.
    """

    def __init__(self, host: str = "http://127.0.0.1:9200"):
        self.host = host
        self.client = Elasticsearch(
            hosts=[host],
            verify_certs=False,
            request_timeout=10,
            retry_on_timeout=True,
            max_retries=3,
        )

    def ping(self) -> bool:
        """Verifies cluster connectivity."""
        try:
            return bool(self.client.ping())
        except Exception as e:
            print(f"[DEBUG] Elasticsearch ping failed: {e}")
            return False

    def create_index(self, recreate: bool = True) -> bool:
        """
        Creates the 'distributed_systems_corpus' index with explicit multi-fields:
        - content: standard analyzer (Unicode word boundary, punctuation stripped)
        - content.english: english analyzer (Porter stemming + English stopwords)
        - content.exact: custom pattern analyzer (preserves hyphenated compound terms)
        """
        if self.client.indices.exists(index=INDEX_NAME):
            if recreate:
                self.client.indices.delete(index=INDEX_NAME)
                print(f"[INDEX] Deleted existing index '{INDEX_NAME}'.")
            else:
                print(f"[INDEX] Index '{INDEX_NAME}' already exists.")
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
        """
        Bulk ingests documents and enforces the Near-Real-Time (NRT) Refresh Gate.
        """
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

        # THE NRT REFRESH GATE: Commit in-memory buffers to searchable OS page-cache segments
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
        """
        Executes full-text match query on the specified field with specified boolean operator.
        Returns list of (doc_id, bm25_score, title) tuples sorted descending by score.
        """
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
        """Fetches Lucene's internal BM25 scoring tree via the _explain API."""
        query_body = {"query": {"match": {field: query}}}
        return self.client.explain(
            index=INDEX_NAME, id=str(doc_id), body=query_body
        )


if __name__ == "__main__":
    client = ElasticsearchClient()
    if not client.ping():
        print("[ERROR] Cannot reach Elasticsearch on 127.0.0.1:9200.")
        print("        Please ensure 'docker compose up -d' is running.")
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

"""
generate_judgments.py - Ground Truth Relevance Judgment Generator.

Constructs relevance_judgments.json for the Information Retrieval evaluation suite.
In IR science (the Cranfield / TREC paradigm), search relevance is probabilistic
and requires human expert truth labels (grades 0, 1, 2) to compute Precision,
Recall, Mean Reciprocal Rank (MRR), and Mean Average Precision (MAP).
"""

import json
from pathlib import Path

JUDGMENTS_DATA = {
    "metadata": {
        "framework": "Cranfield / TREC Information Retrieval Evaluation Paradigm",
        "purpose": "Ground truth relevance annotations for Dual-Engine Benchmark Comparison",
        "scale": {
            "0": "Irrelevant - Document does not address the query information need.",
            "1": "Partially Relevant - Document covers tangential or related sub-topics.",
            "2": "Highly Relevant - Document directly answers or centers on the query topic.",
        },
    },
    "queries": [
        {
            "query_id": "A1",
            "query": "replicating cache nodes",
            "category": "Elasticsearch Win (Morphological Suffix Stemming)",
            "expected_top_docs": [1, 8],
            "judgments": [
                {
                    "doc_id": 1,
                    "grade": 2,
                    "notes": "Highly relevant: Directly covers distributed cache nodes and replication.",
                },
                {
                    "doc_id": 8,
                    "grade": 1,
                    "notes": "Partially relevant: Covers data replication strategies (sync vs async).",
                },
                {
                    "doc_id": 9,
                    "grade": 1,
                    "notes": "Partially relevant: Verbose document mentioning cache cluster repeatedly.",
                },
            ],
        },
        {
            "query_id": "A2",
            "query": "redis cache clustering and sharding",
            "category": "Elasticsearch Win (Disjunctive Multi-Term Candidate Retrieval)",
            "expected_top_docs": [1, 3],
            "judgments": [
                {
                    "doc_id": 1,
                    "grade": 2,
                    "notes": "Highly relevant: Covers Redis in-memory cache and cluster acceleration.",
                },
                {
                    "doc_id": 3,
                    "grade": 2,
                    "notes": "Highly relevant: Dedicated document on database sharding and partitioning.",
                },
                {
                    "doc_id": 5,
                    "grade": 1,
                    "notes": "Partially relevant: Consistent hashing for cache server distribution.",
                },
            ],
        },
        {
            "query_id": "A3",
            "query": "how to achieve consensus across distributed servers",
            "category": "Elasticsearch Win (Conversational Stopword Filtering & IDF)",
            "expected_top_docs": [2],
            "judgments": [
                {
                    "doc_id": 2,
                    "grade": 2,
                    "notes": "Highly relevant: Explains Raft consensus across distributed servers.",
                },
                {
                    "doc_id": 6,
                    "grade": 1,
                    "notes": "Partially relevant: Covers distributed transactions and 2PC coordinator.",
                },
            ],
        },
        {
            "query_id": "B1",
            "query": "in-memory key-value",
            "category": "Homemade Index Win (Hyphenated Compound Term Preservation)",
            "expected_top_docs": [1],
            "judgments": [
                {
                    "doc_id": 1,
                    "grade": 2,
                    "notes": "Highly relevant: Explicitly introduces Redis as an in-memory key-value store.",
                }
            ],
        },
        {
            "query_id": "B2",
            "query": "scatter-gather",
            "category": "Homemade Index Win (Small-Corpus Exact Float IDF Separation)",
            "expected_top_docs": [3],
            "judgments": [
                {
                    "doc_id": 3,
                    "grade": 2,
                    "notes": "Highly relevant: Explicitly covers cross-shard scatter-gather queries.",
                }
            ],
        },
        {
            "query_id": "B3",
            "query": "raft consensus leader election",
            "category": "Homemade Index Win (Microsecond Zero-Overhead RAM Execution)",
            "expected_top_docs": [2],
            "judgments": [
                {
                    "doc_id": 2,
                    "grade": 2,
                    "notes": "Highly relevant: Core document on Raft consensus and leader election.",
                },
                {
                    "doc_id": 6,
                    "grade": 1,
                    "notes": "Partially relevant: Mentions distributed coordination for two-phase commit.",
                },
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

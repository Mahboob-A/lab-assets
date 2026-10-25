"""
main.py - Master Search Engine Execution and Evaluation Pipeline.

Orchestrates the entire homemade search engine:
1. Ingests and indexes the corpus.
2. Demonstrates the TF-IDF vs. Okapi BM25 ranking flip.
3. Executes 5 benchmark queries and logs results to query_log.txt.
4. Generates relevance_judgments.json as the ground truth benchmark for Lab 3.
"""

import json
from analyzer import TextAnalyzer
from corpus import DOCUMENTS
from index import InvertedIndex
from ranker import SearchRanker


def build_search_engine():
    """Initializes and populates the inverted index."""
    analyzer = TextAnalyzer()
    index = InvertedIndex()

    print("=" * 70)
    print("STEP 1: Indexing Document Collection")
    print("=" * 70)

    for doc_id, doc_data in DOCUMENTS.items():
        tokens = analyzer.analyze(doc_data["content"])
        index.add_document(doc_id, tokens)
        print(f"  [Indexed] Doc {doc_id:02d}: '{doc_data['title']}' ({len(tokens)} tokens)")

    print("-" * 70)
    print(f"Corpus Statistics:")
    print(f"  • Total Documents:     {index.total_docs}")
    print(f"  • Vocabulary Size:      {index.vocabulary_size()} unique terms")
    print(f"  • Average Doc Length:   {index.avg_doc_len:.2f} tokens")
    print("=" * 70)

    ranker = SearchRanker(index, analyzer)
    return analyzer, index, ranker


def demonstrate_ranking_flip(ranker: SearchRanker):
    """
    Runs a side-by-side comparison showing why BM25 is superior to raw TF-IDF.
    Compares Doc 1 (concise, focused) against Doc 9 (verbose, keyword-stuffed).
    """
    test_query = "distributed cache"
    print("\n" + "=" * 70)
    print("STEP 2: The Ranking Demonstration (TF-IDF vs. Okapi BM25)")
    print(f"Query: '{test_query}'")
    print("=" * 70)

    # 1. TF-IDF Ranking
    tfidf_results = ranker.search(test_query, mode="tfidf", top_k=5)
    print("\n--- Mode A: Classic TF-IDF Scoring ---")
    print("Rank | Doc ID | Score   | Title")
    print("-----+--------+---------+----------------------------------------------")
    for rank, (doc_id, score) in enumerate(tfidf_results, start=1):
        title = DOCUMENTS[doc_id]["title"]
        print(f"  {rank}  | Doc {doc_id:02d} | {score:7.4f} | {title}")
    print("Notice: Doc 9 (the verbose document) unfairly takes Rank #1 in TF-IDF")
    print("because it repeats 'cache' 8+ times (keyword stuffing effect)!")

    # 2. Okapi BM25 Ranking
    bm25_results = ranker.search(test_query, mode="bm25", top_k=5)
    print("\n--- Mode B: Okapi BM25 Scoring (k1=1.2, b=0.75) ---")
    print("Rank | Doc ID | Score   | Title")
    print("-----+--------+---------+----------------------------------------------")
    for rank, (doc_id, score) in enumerate(bm25_results, start=1):
        title = DOCUMENTS[doc_id]["title"]
        print(f"  {rank}  | Doc {doc_id:02d} | {score:7.4f} | {title}")
    print("Notice: BM25 flips the ranking! Doc 1 is promoted to Rank #1 because")
    print("BM25 saturates term frequency (k1) and penalizes long, rambling documents (b).")

    # 3. Explain Score Inspection
    print("\n--- Term-by-Term Score Explanation (Elasticsearch _explain style) ---")
    exp_doc1 = ranker.explain_score(test_query, 1)
    exp_doc9 = ranker.explain_score(test_query, 9)

    print(f"\nDoc 1 (length={exp_doc1['doc_len']}, len_ratio={exp_doc1['len_ratio']}):")
    for t in exp_doc1["terms"]:
        print(f"  • term '{t['term']}': tf={t['tf']}, idf={t['idf']}, tf_norm={t['tf_norm']} -> BM25 contribution={t['bm25_contribution']}")
    print(f"  => Total BM25: {exp_doc1['bm25_score']} | Total TF-IDF: {exp_doc1['tfidf_score']}")

    print(f"\nDoc 9 (length={exp_doc9['doc_len']}, len_ratio={exp_doc9['len_ratio']}):")
    for t in exp_doc9["terms"]:
        print(f"  • term '{t['term']}': tf={t['tf']}, idf={t['idf']}, tf_norm={t['tf_norm']} -> BM25 contribution={t['bm25_contribution']}")
    print(f"  => Total BM25: {exp_doc9['bm25_score']} | Total TF-IDF: {exp_doc9['tfidf_score']}")
    print("=" * 70)


def run_benchmark_queries(ranker: SearchRanker):
    """
    Executes the 5 benchmark queries and writes results to query_log.txt.
    """
    benchmark_queries = [
        "distributed cache replication",
        "raft consensus leader election",
        "database sharding partition key",
        "kafka event streaming partition",
        "consistent hashing virtual nodes",
    ]

    log_lines = []
    log_lines.append("======================================================================")
    log_lines.append("INVERTED INDEX ENGINE: BENCHMARK QUERY LOG")
    log_lines.append("======================================================================\n")

    print("\n" + "=" * 70)
    print("STEP 3: Running 5 Benchmark Queries")
    print("=" * 70)

    for i, query in enumerate(benchmark_queries, start=1):
        results = ranker.search(query, mode="bm25", top_k=5)
        header = f"Query {i}: '{query}'"
        print(f"\n{header}")
        log_lines.append(header)
        log_lines.append("-" * 70)

        if not results:
            msg = "  No matching documents found."
            print(msg)
            log_lines.append(msg)
        else:
            for rank, (doc_id, score) in enumerate(results, start=1):
                title = DOCUMENTS[doc_id]["title"]
                line = f"  Rank {rank}: Doc {doc_id:02d} [BM25: {score:.4f}] - {title}"
                print(line)
                log_lines.append(line)

        log_lines.append("")

    # Save to query_log.txt
    with open("query_log.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines))

    print("\n[Saved] Query log exported to: query_log.txt")


def generate_relevance_judgments():
    """
    Generates relevance_judgments.json mapping standard queries to human-scored
    relevance assessments (0 = Irrelevant, 1 = Partially Relevant, 2 = Highly Relevant).
    This file serves as the ground truth evaluation benchmark for Module 16a Lab 3.
    """
    relevance_data = {
        "metadata": {
            "module": "M16a",
            "lab": "M16a-L1",
            "purpose": "Ground truth relevance judgments for search evaluation and Elasticsearch comparison in M16a-L3",
            "scale": {
                "0": "Irrelevant - Document does not address the query information need.",
                "1": "Partially Relevant - Document touches on the topic or related concepts.",
                "2": "Highly Relevant - Document directly answers or centers on the query topic."
            }
        },
        "queries": [
            {
                "query_id": "Q1",
                "query": "distributed cache replication",
                "expected_top_docs": [1, 8, 9],
                "judgments": [
                    {"doc_id": 1, "grade": 2, "notes": "Highly relevant: Directly covers Redis as a distributed cache with master-slave replication."},
                    {"doc_id": 8, "grade": 1, "notes": "Partially relevant: Explains synchronous vs asynchronous replication, but not caching."},
                    {"doc_id": 9, "grade": 1, "notes": "Partially relevant: Mentions distributed cache repeatedly, but rambling/noisy."}
                ]
            },
            {
                "query_id": "Q2",
                "query": "raft consensus leader election",
                "expected_top_docs": [2],
                "judgments": [
                    {"doc_id": 2, "grade": 2, "notes": "Highly relevant: Specifically explains Raft consensus states and leader election timers."},
                    {"doc_id": 6, "grade": 1, "notes": "Partially relevant: Covers distributed transactions, but not Raft consensus."}
                ]
            },
            {
                "query_id": "Q3",
                "query": "database sharding partition key",
                "expected_top_docs": [3],
                "judgments": [
                    {"doc_id": 3, "grade": 2, "notes": "Highly relevant: Core focus on horizontal sharding, partition keys, and avoiding hotspots."},
                    {"doc_id": 5, "grade": 1, "notes": "Partially relevant: Discusses consistent hashing key mapping, but for cache nodes."}
                ]
            },
            {
                "query_id": "Q4",
                "query": "kafka event streaming partition",
                "expected_top_docs": [4],
                "judgments": [
                    {"doc_id": 4, "grade": 2, "notes": "Highly relevant: Directly covers Apache Kafka event streaming and partitioned logs."},
                    {"doc_id": 3, "grade": 1, "notes": "Partially relevant: Mentions partitioning, but in relational databases."}
                ]
            },
            {
                "query_id": "Q5",
                "query": "consistent hashing virtual nodes",
                "expected_top_docs": [5],
                "judgments": [
                    {"doc_id": 5, "grade": 2, "notes": "Highly relevant: Dedicated document explaining consistent hashing and virtual node distribution."},
                    {"doc_id": 1, "grade": 1, "notes": "Partially relevant: Covers caching, but not consistent hashing ring geometry."}
                ]
            }
        ]
    }

    with open("relevance_judgments.json", "w", encoding="utf-8") as f:
        json.dump(relevance_data, f, indent=2)

    print("[Saved] Relevance judgments exported to: relevance_judgments.json")


def main():
    analyzer, index, ranker = build_search_engine()
    demonstrate_ranking_flip(ranker)
    run_benchmark_queries(ranker)
    generate_relevance_judgments()
    print("\n" + "=" * 70)
    print("ALL STEPS COMPLETED SUCCESSFULLY!")
    print("=" * 70)


if __name__ == "__main__":
    main()

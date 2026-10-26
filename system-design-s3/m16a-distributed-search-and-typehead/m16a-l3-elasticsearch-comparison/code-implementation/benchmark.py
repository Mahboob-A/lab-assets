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

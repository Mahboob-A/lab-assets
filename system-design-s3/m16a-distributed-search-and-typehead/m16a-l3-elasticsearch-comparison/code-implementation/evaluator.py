"""
evaluator.py - Information Retrieval Evaluation Engine.

Computes standard Information Retrieval (IR) metrics:
- Precision@k: (Relevant retrieved in top k) / k
- Recall@k:    (Relevant retrieved in top k) / (Total relevant in corpus)
- Reciprocal Rank (RR): 1 / rank_of_first_relevant
- Average Precision (AP): Area under the precision-recall curve
- Mean Reciprocal Rank (MRR): Average RR across query suite
- Mean Average Precision (MAP): Average AP across query suite
"""

import json
from pathlib import Path


class SearchEvaluator:
    """
    Evaluates search engine rankings against ground-truth relevance judgments.
    """

    def __init__(self, judgments_path: str = None):
        if judgments_path is None:
            judgments_path = str(Path(__file__).parent / "relevance_judgments.json")
        self.judgments_path = judgments_path
        self.data = self._load_judgments()
        self.queries_map = {q["query"]: q for q in self.data.get("queries", [])}
        self.query_id_map = {q["query_id"]: q for q in self.data.get("queries", [])}

    def _load_judgments(self) -> dict:
        """Loads relevance judgments from JSON file."""
        with open(self.judgments_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def get_relevant_docs(self, query: str, min_grade: int = 1) -> set[int]:
        """
        Returns the set of relevant document IDs for a given query text.
        By default, any document with grade >= 1 is considered relevant.
        """
        query_entry = self.queries_map.get(query)
        if not query_entry:
            # Try finding by query_id if passed
            query_entry = self.query_id_map.get(query)
        if not query_entry:
            return set()

        return {
            j["doc_id"]
            for j in query_entry.get("judgments", [])
            if j.get("grade", 0) >= min_grade
        }

    @staticmethod
    def precision_at_k(retrieved: list[int], relevant: set[int], k: int) -> float:
        """
        Computes Precision@k: fraction of top-k retrieved documents that are relevant.
        Formula: (Count of relevant docs in top k) / k
        """
        if k <= 0:
            return 0.0
        top_k = retrieved[:k]
        if not top_k:
            return 0.0
        relevant_in_top_k = sum(1 for doc_id in top_k if doc_id in relevant)
        return round(relevant_in_top_k / k, 4)

    @staticmethod
    def recall_at_k(retrieved: list[int], relevant: set[int], k: int) -> float:
        """
        Computes Recall@k: fraction of all relevant documents retrieved in top-k.
        Formula: (Count of relevant docs in top k) / (Total relevant docs in corpus)
        """
        if not relevant:
            return 0.0
        if k <= 0:
            return 0.0
        top_k = retrieved[:k]
        relevant_in_top_k = sum(1 for doc_id in top_k if doc_id in relevant)
        return round(relevant_in_top_k / len(relevant), 4)

    @staticmethod
    def reciprocal_rank(retrieved: list[int], relevant: set[int]) -> float:
        """
        Computes Reciprocal Rank (RR): 1 / rank of the first relevant document.
        Returns 0.0 if no relevant document is retrieved.
        """
        if not relevant:
            return 0.0
        for rank, doc_id in enumerate(retrieved, start=1):
            if doc_id in relevant:
                return round(1.0 / rank, 4)
        return 0.0

    @staticmethod
    def average_precision(retrieved: list[int], relevant: set[int]) -> float:
        """
        Computes Average Precision (AP):
        Sum of Precision@k for every rank k where retrieved[k] is relevant,
        divided by the total number of relevant documents.
        Formula: AP = (1 / |Relevant|) * Sum_{k=1}^N (P@k * rel(k))
        """
        if not relevant:
            return 0.0
        if not retrieved:
            return 0.0

        hits = 0
        sum_precisions = 0.0

        for rank, doc_id in enumerate(retrieved, start=1):
            if doc_id in relevant:
                hits += 1
                precision_at_this_rank = hits / rank
                sum_precisions += precision_at_this_rank

        return round(sum_precisions / len(relevant), 4)

    def evaluate_retrieval(
        self,
        query: str,
        retrieved: list[int],
        k_values: list[int] = None,
        min_grade: int = 1,
    ) -> dict:
        """
        Evaluates a single query's retrieved document IDs across standard metrics.
        """
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
        """
        Aggregates metrics across an entire query suite to produce:
        - Mean Average Precision (MAP)
        - Mean Reciprocal Rank (MRR)
        - Mean Precision@1, 3, 5, 10
        - Mean Recall@1, 3, 5, 10
        """
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

        # Average P@k and R@k
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
    print(f"Loaded {len(evaluator.queries_map)} queries from judgments file.")

    # Test Query A1
    q_a1 = "replicating cache nodes"
    rel_a1 = evaluator.get_relevant_docs(q_a1)
    print(f"\nQuery: '{q_a1}'")
    print(f"Relevant docs: {rel_a1}")

    # Simulated Run 1: Perfect retrieval [1, 8]
    perf_eval = evaluator.evaluate_retrieval(q_a1, [1, 8, 3])
    print("\nSimulated Perfect Retrieval [1, 8, 3]:")
    print(f"  P@1: {perf_eval['precision@1']}, P@3: {perf_eval['precision@3']}")
    print(f"  R@1: {perf_eval['recall@1']}, R@3: {perf_eval['recall@3']}")
    print(f"  RR:  {perf_eval['reciprocal_rank']}, AP:  {perf_eval['average_precision']}")

    # Simulated Run 2: Empty retrieval [] (Homemade Index failure case)
    empty_eval = evaluator.evaluate_retrieval(q_a1, [])
    print("\nSimulated Empty Retrieval []:")
    print(f"  P@1: {empty_eval['precision@1']}, R@10: {empty_eval['recall@10']}")
    print(f"  RR:  {empty_eval['reciprocal_rank']}, AP:  {empty_eval['average_precision']}")

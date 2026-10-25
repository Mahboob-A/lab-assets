"""
ranker.py - Boolean Retrieval and Relevance Ranking Engine.

Implements two-pointer sorted posting list intersection for fast candidate matching,
classic TF-IDF, and industry-standard Okapi BM25 scoring with explainability tools.
"""

import math
from analyzer import TextAnalyzer
from index import InvertedIndex


class SearchRanker:
    """
    Retrieves and ranks documents using an InvertedIndex and TextAnalyzer.
    """

    def __init__(self, index: InvertedIndex, analyzer: TextAnalyzer):
        self.index = index
        self.analyzer = analyzer

    # 1. Fast Boolean Candidate Retrieval (Two-Pointer Intersection)
    def intersect_two_postings(
        self, list_a: list[tuple[int, int]], list_b: list[tuple[int, int]]
    ) -> list[int]:
        """
        Computes the Boolean AND intersection of two sorted posting lists
        using a two-pointer linear merge scan in O(len(A) + len(B)) time.
        """
        p1 = 0
        p2 = 0
        matching_docs = []

        while p1 < len(list_a) and p2 < len(list_b):
            doc_a = list_a[p1][0]
            doc_b = list_b[p2][0]

            if doc_a == doc_b:
                matching_docs.append(doc_a)
                p1 += 1
                p2 += 1
            elif doc_a < doc_b:
                p1 += 1
            else:
                p2 += 1

        return matching_docs

    def get_candidates(self, query_tokens: list[str]) -> list[int]:
        """
        Finds all documents containing ALL query tokens (Boolean AND).
        Optimized by sorting posting lists by length ascending to minimize comparisons.
        """
        if not query_tokens:
            return []

        # Fetch posting lists for each query term
        posting_lists = [self.index.get_postings(term) for term in query_tokens]

        # If any term is not present in the index at all, AND intersection is empty
        if any(len(p) == 0 for p in posting_lists):
            return []

        # Sort posting lists by length ascending (shortest list first)
        posting_lists.sort(key=len)

        # Start with the shortest posting list's document IDs
        candidates = [doc_id for doc_id, _ in posting_lists[0]]

        # Intersect progressively with the remaining lists
        for next_postings in posting_lists[1:]:
            # Convert current candidate list into dummy postings for intersection
            current_postings = [(doc_id, 0) for doc_id in candidates]
            candidates = self.intersect_two_postings(current_postings, next_postings)
            if not candidates:
                break

        return candidates

    # 2. Relevance Scoring: IDF, TF-IDF, and Okapi BM25
    def calculate_idf(self, term: str) -> float:
        """
        Calculates the Inverse Document Frequency (IDF) using the standard
        Lucene / Okapi BM25 formula:
        IDF(t) = ln(1 + (N - DF + 0.5) / (DF + 0.5))
        """
        doc_count = self.index.total_docs
        df = self.index.get_doc_freq(term)
        if df == 0 or doc_count == 0:
            return 0.0

        return math.log(1.0 + (doc_count - df + 0.5) / (df + 0.5))

    def _get_term_frequency(self, term: str, doc_id: int) -> int:
        """Helper to look up term frequency of a term in a specific document."""
        for d_id, freq in self.index.get_postings(term):
            if d_id == doc_id:
                return freq
        return 0

    def score_tf_idf(self, query_tokens: list[str], doc_id: int) -> float:
        """
        Calculates classic TF-IDF score:
        Score = sum( TF(t, doc) * IDF(t) )
        Subject to linear term frequency growth and document length bias.
        """
        total_score = 0.0
        for term in query_tokens:
            tf = self._get_term_frequency(term, doc_id)
            idf = self.calculate_idf(term)
            total_score += tf * idf
        return total_score

    def score_bm25(
        self,
        query_tokens: list[str],
        doc_id: int,
        k1: float = 1.2,
        b: float = 0.75,
    ) -> float:
        """
        Calculates Okapi BM25 score:
        - k1 (term saturation, default 1.2): caps diminishing returns from repeated words.
        - b (length normalization, default 0.75): balances short vs long documents against avgdl.
        """
        total_score = 0.0
        doc_len = self.index.get_doc_len(doc_id)
        avg_len = self.index.avg_doc_len

        # Length normalization factor: (1 - b + b * (doc_len / avg_len))
        len_norm = 1.0 - b + b * (doc_len / avg_len) if avg_len > 0 else 1.0

        for term in query_tokens:
            tf = self._get_term_frequency(term, doc_id)
            if tf == 0:
                continue

            idf = self.calculate_idf(term)

            # Saturated, normalized TF term: (tf * (k1 + 1)) / (tf + k1 * len_norm)
            tf_norm = (tf * (k1 + 1.0)) / (tf + k1 * len_norm)

            total_score += idf * tf_norm

        return total_score

    def explain_score(
        self,
        query: str,
        doc_id: int,
        k1: float = 1.2,
        b: float = 0.75,
    ) -> dict:
        """
        Provides a transparent, term-by-term mathematical breakdown of the
        BM25 score for a document, modeled after Elasticsearch's _explain API.
        """
        tokens = self.analyzer.analyze(query)
        doc_len = self.index.get_doc_len(doc_id)
        avg_len = self.index.avg_doc_len
        len_norm = 1.0 - b + b * (doc_len / avg_len) if avg_len > 0 else 1.0

        breakdown = {
            "doc_id": doc_id,
            "doc_len": doc_len,
            "avg_len": round(avg_len, 2),
            "len_ratio": round(doc_len / avg_len, 2) if avg_len > 0 else 1.0,
            "terms": [],
            "bm25_score": 0.0,
            "tfidf_score": 0.0,
        }

        bm25_total = 0.0
        tfidf_total = 0.0

        for term in tokens:
            tf = self._get_term_frequency(term, doc_id)
            idf = self.calculate_idf(term)
            tf_norm = (tf * (k1 + 1.0)) / (tf + k1 * len_norm) if tf > 0 else 0.0
            term_bm25 = idf * tf_norm
            term_tfidf = tf * idf

            bm25_total += term_bm25
            tfidf_total += term_tfidf

            breakdown["terms"].append(
                {
                    "term": term,
                    "tf": tf,
                    "idf": round(idf, 4),
                    "tf_norm": round(tf_norm, 4),
                    "bm25_contribution": round(term_bm25, 4),
                    "tfidf_contribution": round(term_tfidf, 4),
                }
            )

        breakdown["bm25_score"] = round(bm25_total, 4)
        breakdown["tfidf_score"] = round(tfidf_total, 4)
        return breakdown

    # 3. Unified Search Surface
    def search(
        self,
        query: str,
        mode: str = "bm25",
        top_k: int = 10,
        k1: float = 1.2,
        b: float = 0.75,
    ) -> list[tuple[int, float]]:
        """
        End-to-end search pipeline:
        1. Analyzes query using TextAnalyzer.
        2. Retrieves candidate Doc IDs via Boolean AND intersection.
        3. Ranks candidates using specified scoring mode ('bm25' or 'tfidf').
        4. Returns top-K results sorted by score descending.
        """
        tokens = self.analyzer.analyze(query)
        candidates = self.get_candidates(tokens)

        if not candidates:
            return []

        ranked_results = []
        for doc_id in candidates:
            if mode == "bm25":
                score = self.score_bm25(tokens, doc_id, k1=k1, b=b)
            elif mode == "tfidf":
                score = self.score_tf_idf(tokens, doc_id)
            else:
                raise ValueError(
                    f"Unknown scoring mode: '{mode}'. Use 'bm25' or 'tfidf'."
                )

            ranked_results.append((doc_id, score))

        # Sort descending by score
        ranked_results.sort(key=lambda x: x[1], reverse=True)
        return ranked_results[:top_k]

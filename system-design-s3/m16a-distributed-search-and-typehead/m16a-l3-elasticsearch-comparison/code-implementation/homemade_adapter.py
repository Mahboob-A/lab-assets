"""
homemade_adapter.py - Adapter for the Custom Inverted Index from Lab 1.

Encapsulates TextAnalyzer, InvertedIndex, and SearchRanker behind a standardized
engine interface compatible with ElasticsearchClient output format.
"""

from analyzer import TextAnalyzer
from corpus import DOCUMENTS
from index import InvertedIndex
from ranker import SearchRanker


class HomemadeSearchEngine:
    """
    Adapter providing a clean search interface over the custom Python
    Inverted Index built in Module 16a, Lab 1.
    """

    def __init__(self, documents: dict[int, dict] = None):
        self.documents = documents if documents is not None else DOCUMENTS
        self.analyzer = TextAnalyzer()
        self.index = InvertedIndex()
        self._build_index()
        self.ranker = SearchRanker(self.index, self.analyzer)

    def _build_index(self) -> None:
        """Indexes all documents from the corpus into the custom inverted index."""
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
        """
        Executes search and returns standardized result dictionaries:
        [
            {
                "doc_id": int,
                "score": float,
                "title": str,
                "content_preview": str,
            },
            ...
        ]
        """
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
        """Returns term-by-term BM25 score calculation breakdown."""
        return self.ranker.explain_score(query, doc_id)


if __name__ == "__main__":
    print("=" * 65)
    print("Self-Test: HomemadeSearchEngine Adapter")
    print("=" * 65)

    engine = HomemadeSearchEngine()
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

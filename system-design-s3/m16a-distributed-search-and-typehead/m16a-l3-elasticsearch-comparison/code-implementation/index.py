"""
index.py - Inverted Index Data Structure.

Maintains the Lexicon (Dictionary) and sorted Posting Lists.
Tracks corpus-level metadata (total documents, document lengths, and avgdl)
required for BM25 length normalization.
"""

from collections import Counter, defaultdict


class InvertedIndex:
    """
    An in-memory inverted index mapping normalized terms to sorted posting lists.
    Each posting is a tuple: (doc_id, term_frequency).
    """

    def __init__(self):
        # Dictionary: term -> [(doc_id, tf), ...]
        self.dictionary: dict[str, list[tuple[int, int]]] = defaultdict(list)
        # Document length table: doc_id -> token count
        self.doc_lengths: dict[int, int] = {}
        self.total_docs: int = 0
        self.total_tokens: int = 0

    @property
    def avg_doc_len(self) -> float:
        """Computes the average document length (avgdl) across the corpus."""
        if self.total_docs == 0:
            return 0.0
        return self.total_tokens / self.total_docs

    def add_document(self, doc_id: int, tokens: list[str]) -> None:
        """
        Indexes a single document.
        Calculates term frequencies and appends postings.
        Because doc_id is ingested in increasing order, posting lists
        are guaranteed to remain strictly sorted by doc_id.
        """
        doc_len = len(tokens)
        self.total_docs += 1
        self.total_tokens += doc_len
        self.doc_lengths[doc_id] = doc_len

        # Count occurrences of each term in this document
        tf_counts = Counter(tokens)

        # Append (doc_id, tf) to each term's posting list
        for term, freq in tf_counts.items():
            self.dictionary[term].append((doc_id, freq))

    def get_postings(self, term: str) -> list[tuple[int, int]]:
        """Returns the posting list for a given term, or empty list if term is unknown."""
        return self.dictionary.get(term, [])

    def get_doc_freq(self, term: str) -> int:
        """Returns the Document Frequency (DF) - how many documents contain this term."""
        return len(self.dictionary.get(term, []))

    def get_doc_len(self, doc_id: int) -> int:
        """Returns the length (token count) of a specific document."""
        return self.doc_lengths.get(doc_id, 0)

    def vocabulary_size(self) -> int:
        """Returns the total number of unique terms in the dictionary."""
        return len(self.dictionary)


if __name__ == "__main__":
    # Quick self-test for InvertedIndex
    index = InvertedIndex()
    index.add_document(1, ["redis", "distributed", "cache"])
    index.add_document(2, ["raft", "consensus", "leader"])
    index.add_document(3, ["distributed", "raft", "cache"])

    print(f"Total docs: {index.total_docs}")
    print(f"Average document length: {index.avg_doc_len:.2f}")
    print(f"Postings for 'cache': {index.get_postings('cache')}")
    print(f"Postings for 'distributed': {index.get_postings('distributed')}")

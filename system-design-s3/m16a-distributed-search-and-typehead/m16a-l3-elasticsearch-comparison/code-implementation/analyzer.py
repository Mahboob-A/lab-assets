"""
analyzer.py - Text Ingestion and Analysis Pipeline.

Responsible for converting raw text into clean, normalized tokens.
Follows the Iron Rule of Search: Queries and documents must pass
through the exact same analysis pipeline to guarantee vocabulary consistency.
"""

import re


class TextAnalyzer:
    """
    Splits raw text into normalized linguistic tokens by lowercasing,
    extracting alphanumeric terms, and stripping common stop words.
    """

    # Common English functional words that add no discriminant search value
    STOPWORDS = {
        "a", "an", "the", "and", "or", "in", "on", "at", "to", "for",
        "of", "with", "by", "from", "is", "are", "was", "were", "it",
        "this", "that", "these", "those", "into", "as", "be", "your",
        "we", "when", "all", "can", "if", "often", "where", "how"
    }

    def __init__(self, stopwords: set[str] | None = None):
        self.stopwords = stopwords if stopwords is not None else self.STOPWORDS

    def tokenize(self, text: str) -> list[str]:
        """
        Extracts words using regular expression word boundaries.
        Matches alphanumeric words and keeps hyphenated terms intact.
        """
        return re.findall(r"\b[a-zA-Z0-9_-]+\b", text)

    def normalize(self, tokens: list[str]) -> list[str]:
        """
        Normalizes extracted tokens: converts to lowercase and filters
        out stop words and pure punctuation noise.
        """
        # Lowercase and filter out stop words
        normalized = [
            token.lower()
            for token in tokens
            if token.lower() not in self.stopwords and len(token) > 1
        ]
        return normalized

    def analyze(self, text: str) -> list[str]:
        """
        Full analysis pipeline: raw text -> tokenization -> normalization.
        """
        raw_tokens = self.tokenize(text)
        return self.normalize(raw_tokens)


if __name__ == "__main__":
    # Quick self-test for TextAnalyzer
    analyzer = TextAnalyzer()
    sample_text = "Redis is an in-memory, key-value distributed cache!"
    tokens = analyzer.analyze(sample_text)
    print(f"Sample Input:  '{sample_text}'")
    print(f"Analyzed Tokens: {tokens}")

"""
Memory-Optimized Trie Node Entity.

Uses Python __slots__ to eliminate the dynamic __dict__ overhead on millions of nodes,
reducing memory consumption from ~200+ bytes to ~56 bytes per node.
"""

from typing import Dict, List, Tuple, Optional
import sys


class TrieNode:
    """
    A single character node in the Prefix Trie.
    
    Attributes:
        children: Mapping from character to next child TrieNode.
        is_end: Boolean indicating if a complete search query terminates here.
        frequency: Search frequency count (greater than 0 if is_end is True).
        word: The complete query string terminating at this node (if is_end is True).
        top_k: Precomputed list of top-K (query, frequency) tuples cached at this node.
    """

    __slots__ = ("children", "is_end", "frequency", "word", "top_k")

    def __init__(self):
        self.children: Dict[str, "TrieNode"] = {}
        self.is_end: bool = False
        self.frequency: int = 0
        self.word: Optional[str] = None
        self.top_k: List[Tuple[str, int]] = []

    def byte_size(self) -> int:
        """Calculates the direct memory consumed by this node instance in bytes."""
        size = sys.getsizeof(self)
        size += sys.getsizeof(self.children)
        size += sys.getsizeof(self.top_k)
        if self.word:
            size += sys.getsizeof(self.word)
        return size

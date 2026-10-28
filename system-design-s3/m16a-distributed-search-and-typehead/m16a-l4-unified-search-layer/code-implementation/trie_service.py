"""
Trie-Based Typeahead and Autocomplete Service.

Implements prefix search with node-level Top-K caching populated via a
post-order bottom-up construction traversal using heaps.
"""

from typing import Dict, List, Tuple, Optional
import heapq
import sys
from trie_node import TrieNode


class TrieTypeaheadService:
    """
    Typeahead service powered by a Prefix Tree with precomputed Top-K lists.
    
    Guarantees O(L) suggestion retrieval where L is the prefix length,
    completely avoiding runtime subtree tree walks.
    """

    def __init__(self, k: int = 5):
        self.root = TrieNode()
        self.k = k
        self.total_words = 0

    def insert(self, query: str, frequency: int) -> None:
        """
        Inserts a single query and its frequency into the Trie.
        
        Args:
            query: The search term string.
            frequency: Historical query volume.
        """
        current = self.root
        normalized = query.strip().lower()

        for char in normalized:
            if char not in current.children:
                current.children[char] = TrieNode()
            current = current.children[char]

        current.is_end = True
        current.frequency = frequency
        current.word = normalized
        self.total_words += 1

    def build_from_dataset(self, queries: Dict[str, int], k: Optional[int] = None) -> None:
        """
        Populates the Trie from a dataset and executes bottom-up Top-K caching.
        
        Args:
            queries: Dictionary mapping query strings to historical frequencies.
            k: Top-K count to cache at each node (defaults to self.k).
        """
        if k is not None:
            self.k = k

        for query, freq in queries.items():
            self.insert(query, freq)

        self.build_top_k(self.k)

    def build_top_k(self, k: int = 5) -> None:
        """
        TODO 1: Implement Post-Order Bottom-Up Top-K Caching.
        
        Recursively traverses to leaf nodes first, then merges candidate queries
        from all children plus the current node's own query (if is_end is True).
        Stores the top-K highest-frequency queries in each node's `top_k` attribute.
        """
        def _post_order_dfs(node: TrieNode) -> List[Tuple[str, int]]:
            # Dictionary to deduplicate queries collected from children and self
            candidates: Dict[str, int] = {}

            # 1. Recurse into all children first (bottom-up post-order)
            for child in node.children.values():
                child_top_k = _post_order_dfs(child)
                for query, freq in child_top_k:
                    if query not in candidates or freq > candidates[query]:
                        candidates[query] = freq

            # 2. Critical Edge Case: If a valid word terminates at this node, include it!
            # Example: prefix 'car' terminates at 'car', but child has 'card'.
            # Failing to add node.word would drop 'car' from suggestions.
            if node.is_end and node.word:
                candidates[node.word] = max(node.frequency, candidates.get(node.word, 0))

            # 3. Sort candidates by frequency descending, break ties alphabetically
            sorted_candidates = sorted(
                candidates.items(),
                key=lambda item: (-item[1], item[0])
            )

            # 4. Cache the top-K results directly on this node
            node.top_k = sorted_candidates[:k]
            return node.top_k

        _post_order_dfs(self.root)

    def suggest(self, prefix: str, k: Optional[int] = None) -> List[Tuple[str, int]]:
        """
        TODO 2: Implement Prefix Search with Node-Cached Top-K.
        
        Navigates down the Trie along the prefix characters in O(L) time.
        Once at the prefix node, returns the precomputed top-K list immediately.
        
        Args:
            prefix: The typed character sequence.
            k: Number of suggestions to return (defaults to self.k).
            
        Returns:
            List of (query, frequency) tuples.
        """
        target_k = k if k is not None else self.k
        current = self.root
        normalized = prefix.strip().lower()

        # O(L) character traversal
        for char in normalized:
            if char not in current.children:
                return []  # Prefix does not exist in vocabulary
            current = current.children[char]

        # The prefix node directly holds precomputed Top-K suggestions
        return current.top_k[:target_k]

    def count_nodes_and_memory(self) -> Tuple[int, int]:
        """
        Traverses the full tree to count total TrieNode objects and calculate RAM bytes.
        
        Returns:
            Tuple of (total_node_count, total_memory_bytes).
        """
        node_count = 0
        total_bytes = 0

        stack = [self.root]
        while stack:
            curr = stack.pop()
            node_count += 1
            total_bytes += curr.byte_size()
            for child in curr.children.values():
                stack.append(child)

        return node_count, total_bytes

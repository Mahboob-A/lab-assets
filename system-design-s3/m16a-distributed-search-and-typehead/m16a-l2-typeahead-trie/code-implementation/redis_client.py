"""
Redis Client for Typeahead and Autocomplete Lab.

Provides query logging via atomic Redis Sorted Sets (ZINCRBY) and prefix result caching (SETEX/GET).
Students directly enter Poridhi-provisioned Redis credentials in the configuration variables below.
"""

from typing import List, Tuple, Optional
import json
import redis

# ==============================================================================
# REDIS CREDENTIALS CONFIGURATION
# Students: In Poridhi Lab, enable your Redis database service from the Database
# section and enter the provided host, port, username, and password below.
# ==============================================================================
REDIS_HOST: str = "127.0.0.1"      # Replace with your Poridhi Redis Host (e.g., "100.80.224.229")
REDIS_PORT: int = 6379             # Replace with your Poridhi Redis Port
REDIS_USERNAME: str = "redis"      # Replace with your Poridhi Redis Username
REDIS_PASSWORD: str = "redis"      # Replace with your Poridhi Redis Password
# ==============================================================================


class TypeaheadRedisClient:
    """
    Manages Redis connectivity for typeahead frequency counting and prefix caching.
    
    Provides:
    1. Query Frequency Counter: Logs completed user searches asynchronously via ZINCRBY.
    2. Hot-Prefix Cache: Absorbs repeated prefix keystrokes (e.g. 'sys', 'py') with TTL.
    """

    def __init__(
        self,
        host: str = REDIS_HOST,
        port: int = REDIS_PORT,
        username: str = REDIS_USERNAME,
        password: str = REDIS_PASSWORD,
        socket_timeout: float = 2.0,
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password

        self.client = redis.Redis(
            host=self.host,
            port=self.port,
            username=self.username,
            password=self.password,
            socket_timeout=socket_timeout,
            decode_responses=True,
        )

    def ping(self) -> bool:
        """Checks whether Redis is responsive."""
        return bool(self.client.ping())

    def record_query_search(self, query: str, increment: int = 1) -> float:
        """
        Increments the search frequency for a query atomically using ZINCRBY.
        
        Args:
            query: The full submitted search query.
            increment: Count to add to the historical score.
            
        Returns:
            The new score for the query.
        """
        normalized = query.strip().lower()
        new_score = self.client.zincrby("query_frequencies", increment, normalized)
        return float(new_score)

    def get_top_queries(self, k: int = 10) -> List[Tuple[str, float]]:
        """
        Retrieves the top-K highest-frequency queries from Redis Sorted Set.
        
        Args:
            k: Number of highest-frequency queries to retrieve.
            
        Returns:
            List of (query, frequency) tuples ordered by frequency descending.
        """
        raw = self.client.zrevrange("query_frequencies", 0, k - 1, withscores=True)
        return [(item[0], float(item[1])) for item in raw]

    def cache_prefix_results(
        self,
        prefix: str,
        results: List[Tuple[str, int]],
        ttl_seconds: int = 300,
    ) -> bool:
        """
        Caches top-K prefix suggestions under key 'prefix_cache:{prefix}' with a TTL.
        
        Args:
            prefix: The typed prefix.
            results: List of (query, frequency) suggestions to cache.
            ttl_seconds: Cache lifetime in seconds (default: 300s / 5 mins).
            
        Returns:
            True if cached successfully.
        """
        key = f"prefix_cache:{prefix.strip().lower()}"
        payload = json.dumps(results)
        self.client.setex(key, ttl_seconds, payload)
        return True

    def get_cached_prefix_results(self, prefix: str) -> Optional[List[Tuple[str, int]]]:
        """
        Retrieves cached top-K suggestions for a prefix if present and not expired.
        
        Args:
            prefix: The typed prefix.
            
        Returns:
            List of (query, frequency) suggestions if found, None otherwise.
        """
        key = f"prefix_cache:{prefix.strip().lower()}"
        cached = self.client.get(key)
        if cached:
            return [tuple(item) for item in json.loads(cached)]
        return None

    def flush_test_data(self) -> None:
        """Cleans up keys created during testing."""
        self.client.delete("query_frequencies")
        for key in self.client.scan_iter("prefix_cache:*"):
            self.client.delete(key)

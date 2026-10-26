"""
Redis Client for Typeahead and Autocomplete Lab.

Provides query logging via atomic Redis Sorted Sets (ZINCRBY) and prefix result caching (SETEX/GET).
Students directly enter Poridhi-provisioned Redis credentials in the configuration variables below.
Includes an automatic in-memory dictionary fallback mock if Redis is temporarily unreachable.
"""

from typing import List, Tuple, Optional, Dict
import json
import time

try:
    import redis
except ImportError:
    redis = None

# ==============================================================================
# REDIS CREDENTIALS CONFIGURATION
# Students: If practicing in Poridhi Lab, enable your Redis database service from
# the Database panel and enter your provisioned host, port, and password below.
# For local offline testing without Redis, leave these defaults; the client will
# automatically operate in in-memory fallback mock mode.
# ==============================================================================
REDIS_HOST: str = "127.0.0.1"
REDIS_PORT: int = 6379
REDIS_PASSWORD: Optional[str] = None
# ==============================================================================


class TypeaheadRedisClient:
    """
    Manages Redis connectivity for typeahead frequency counting and prefix caching.
    
    Provides:
    1. Query Frequency Counter: Logs completed user searches asynchronously via ZINCRBY.
    2. Hot-Prefix Cache: Absorbs repeated prefix keystrokes (e.g. 'sys', 'py') with TTL.
    3. Fallback Mock: Emulates operations in memory if Redis is unavailable.
    """

    def __init__(
        self,
        host: str = REDIS_HOST,
        port: int = REDIS_PORT,
        password: Optional[str] = REDIS_PASSWORD,
        socket_timeout: float = 1.0,
    ):
        self.host = host
        self.port = port
        self.password = password
        self._is_mock = False
        self._client: Optional["redis.Redis"] = None

        # Fallback in-memory storage if Redis is offline
        self._mock_kv: Dict[str, Tuple[str, float]] = {}  # key -> (value, expire_timestamp)
        self._mock_zset: Dict[str, float] = {}             # query -> score

        if redis is None:
            self._is_mock = True
            return

        try:
            client = redis.Redis(
                host=self.host,
                port=self.port,
                password=self.password,
                socket_timeout=socket_timeout,
                decode_responses=True,
            )
            client.ping()
            self._client = client
            self._is_mock = False
        except Exception:
            # Fallback gracefully to mock mode without crashing the process
            self._client = None
            self._is_mock = True

    @property
    def is_mock(self) -> bool:
        """Returns True if the client is operating in fallback mock mode."""
        return self._is_mock

    def ping(self) -> bool:
        """Checks whether Redis is responsive."""
        if not self._is_mock and self._client:
            try:
                return bool(self._client.ping())
            except Exception:
                return False
        return True

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
        if not self._is_mock and self._client:
            try:
                new_score = self._client.zincrby("query_frequencies", increment, normalized)
                return float(new_score)
            except Exception:
                pass  # Fall back to mock if Redis throws mid-flight

        # In-memory mock execution
        current = self._mock_zset.get(normalized, 0.0)
        self._mock_zset[normalized] = current + float(increment)
        return self._mock_zset[normalized]

    def get_top_queries(self, k: int = 10) -> List[Tuple[str, float]]:
        """
        Retrieves the top-K highest-frequency queries from Redis Sorted Set.
        
        Args:
            k: Number of highest-frequency queries to retrieve.
            
        Returns:
            List of (query, frequency) tuples ordered by frequency descending.
        """
        if not self._is_mock and self._client:
            try:
                raw = self._client.zrevrange("query_frequencies", 0, k - 1, withscores=True)
                return [(item[0], float(item[1])) for item in raw]
            except Exception:
                pass

        # In-memory mock execution
        sorted_items = sorted(self._mock_zset.items(), key=lambda x: x[1], reverse=True)
        return sorted_items[:k]

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
        if not self._is_mock and self._client:
            try:
                self._client.setex(key, ttl_seconds, payload)
                return True
            except Exception:
                pass

        # In-memory mock execution
        expire_at = time.time() + ttl_seconds
        self._mock_kv[key] = (payload, expire_at)
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
        if not self._is_mock and self._client:
            try:
                cached = self._client.get(key)
                if cached:
                    return [tuple(item) for item in json.loads(cached)]
                return None
            except Exception:
                pass

        # In-memory mock execution
        if key in self._mock_kv:
            payload, expire_at = self._mock_kv[key]
            if time.time() < expire_at:
                return [tuple(item) for item in json.loads(payload)]
            else:
                del self._mock_kv[key]
        return None

    def flush_test_data(self) -> None:
        """Cleans up keys created during testing."""
        if not self._is_mock and self._client:
            try:
                self._client.delete("query_frequencies")
                # Pattern match to delete prefix cache keys
                for key in self._client.scan_iter("prefix_cache:*"):
                    self._client.delete(key)
            except Exception:
                pass
        self._mock_kv.clear()
        self._mock_zset.clear()

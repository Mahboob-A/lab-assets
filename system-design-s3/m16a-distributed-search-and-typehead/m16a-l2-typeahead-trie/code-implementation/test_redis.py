"""
Redis Connectivity and Operations Test.

Students run this script immediately after entering their Redis host, port,
username, and password in redis_client.py to verify network connectivity and functionality.
"""

import sys
import redis
from redis_client import TypeaheadRedisClient, REDIS_HOST, REDIS_PORT, REDIS_USERNAME


def run_test():
    print("=" * 68)
    print(f"Testing Redis Integration against: {REDIS_HOST}:{REDIS_PORT} (User: {REDIS_USERNAME})")
    print("=" * 68)

    try:
        client = TypeaheadRedisClient()
        ping_ok = client.ping()
        if ping_ok:
            print(f"[SUCCESS] Successfully connected to live Redis server at {REDIS_HOST}:{REDIS_PORT}")
    except redis.AuthenticationError as e:
        print(f"[ERROR] Redis Authentication Failed: {e}")
        print("        Check your REDIS_USERNAME and REDIS_PASSWORD in redis_client.py")
        sys.exit(1)
    except redis.ConnectionError as e:
        print(f"[ERROR] Could not connect to Redis server: {e}")
        print("        Ensure your Redis database is provisioned and running in Poridhi Lab,")
        print("        and verify REDIS_HOST and REDIS_PORT in redis_client.py")
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] Unexpected error connecting to Redis: {e}")
        sys.exit(1)

    # Test 1: PING
    print("[TEST 1/4] PING test passed (Server active: PONG)")

    # Test 2: Cache Prefix Results (SETEX)
    sample_suggestions = [
        ("system design interview", 9850),
        ("systemctl status", 3120),
        ("systems architecture patterns", 1750),
    ]
    cache_ok = client.cache_prefix_results("sys", sample_suggestions, ttl_seconds=60)
    assert cache_ok, "Prefix caching failed"
    print("[TEST 2/4] Cache write test passed (Key: prefix_cache:sys)")

    # Test 3: Get Cached Prefix Results (GET)
    cached = client.get_cached_prefix_results("sys")
    assert cached is not None, "Failed to retrieve cached prefix results"
    assert len(cached) == 3, f"Expected 3 cached results, got {len(cached)}"
    print(f"[TEST 3/4] Cache read test passed (Retrieved {len(cached)} items: '{cached[0][0]}')")

    # Test 4: Atomic Sorted Set Query Counter (ZINCRBY)
    score1 = client.record_query_search("system design interview", increment=1)
    score2 = client.record_query_search("system design interview", increment=4)
    assert score2 == score1 + 4, "Score increment calculation mismatch"
    top_items = client.get_top_queries(k=3)
    assert len(top_items) >= 1, "Failed to fetch top queries"
    print(f"[TEST 4/4] Sorted Set increment test passed (Query: '{top_items[0][0]}', Score: {top_items[0][1]})")

    print("=" * 68)
    print("[ALL TESTS PASSED] Redis integration is operating normally.")
    print("=" * 68)


if __name__ == "__main__":
    run_test()

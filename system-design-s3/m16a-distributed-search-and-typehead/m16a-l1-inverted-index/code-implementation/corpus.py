"""
corpus.py - Sample Document Collection for the Inverted Index Lab.

This module provides a realistic seed corpus covering core distributed
systems topics. Notice that Document 9 is intentionally verbose and
repeats keywords to contrast raw Term Frequency against Okapi BM25 ranking.
"""

DOCUMENTS = {
    1: {
        "title": "Distributed In-Memory Caching with Redis",
        "content": (
            "Redis is an in-memory key-value data store frequently deployed as a distributed cache. "
            "A distributed cache cluster accelerates read-heavy workloads with sub-millisecond latency. "
            "Redis cache instances support master-slave replication and automated key eviction policies."
        ),
    },
    2: {
        "title": "Raft Consensus and Leader Election",
        "content": (
            "The Raft consensus algorithm maintains state machine consistency across distributed servers. "
            "Nodes transition between follower, candidate, and leader states through randomized timers and "
            "heartbeat messages during leader election."
        ),
    },
    3: {
        "title": "Database Sharding and Horizontal Partitioning",
        "content": (
            "Database sharding divides a large dataset across multiple database instances horizontally. "
            "Selecting a high-cardinality partition key avoids hotspots and minimizes expensive "
            "cross-shard scatter-gather queries."
        ),
    },
    4: {
        "title": "Kafka Event Streaming and Partition Rebalancing",
        "content": (
            "Apache Kafka is a distributed event streaming platform organized into append-only partitioned logs. "
            "Consumer groups coordinate message processing and trigger partition rebalancing when worker "
            "instances fail or join."
        ),
    },
    5: {
        "title": "Consistent Hashing and Virtual Nodes",
        "content": (
            "Consistent hashing maps both servers and data keys onto a circular hash ring. "
            "Allocating multiple virtual nodes per physical machine prevents uneven cache data distribution "
            "when servers are added or removed."
        ),
    },
    6: {
        "title": "Distributed Transactions with Two-Phase Commit",
        "content": (
            "Two-phase commit coordinates atomic distributed transactions across heterogeneous databases. "
            "The central coordinator issues prepare and commit commands, ensuring all nodes commit or "
            "abort uniformly despite network partitions."
        ),
    },
    7: {
        "title": "Application Load Balancing with Nginx",
        "content": (
            "Reverse proxy load balancers distribute incoming client HTTP traffic across upstream application pools. "
            "Algorithms like least-connections and active health checks ensure high availability and graceful "
            "failure routing."
        ),
    },
    8: {
        "title": "Data Replication Strategies: Sync vs Async",
        "content": (
            "Storage systems balance durability and latency using synchronous or asynchronous replication. "
            "Synchronous replication guarantees zero data loss at the cost of write latency, whereas asynchronous "
            "replication provides eventual consistency."
        ),
    },
    9: {
        "title": "Rambling Infrastructure Notes on Hardware & Cache (Verbose Document)",
        "content": (
            "During our quarterly infrastructure architecture review, the team discussed hardware upgrades, "
            "rack cooling, power distribution, network switches, operating system kernels, and distributed cache deployment. "
            "When building a distributed cache, engineers must consider disk controllers, memory timing, thermal throttling, "
            "and network interface card bandwidth. A cache can store temporary session tokens, but cache invalidation "
            "remains notoriously difficult across data centers. If the distributed cache experiences packet loss, "
            "database connection pools spike and server response times degrade. Furthermore, we audited backup power supplies, "
            "fiber optic cables, and firewall rule configurations before testing the distributed cache cluster again. "
            "In summary, monitoring CPU utilization and distributed cache metrics is essential for data center reliability."
        ),
    },
}

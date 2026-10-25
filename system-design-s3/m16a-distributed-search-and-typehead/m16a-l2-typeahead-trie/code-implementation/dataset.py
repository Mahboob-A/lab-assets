"""
Query Dataset Generator for Typeahead and Autocomplete Lab.

Provides realistic seed queries across technology domains (System Design, Cloud,
Databases, Languages, DevOps) with Zipfian/power-law frequency distributions.
Supports procedural expansion for large-scale memory and latency benchmarking.
"""

from typing import Dict, List, Tuple
import math
import random


class QueryDataset:
    """Manages search queries and their historical frequencies."""

    # Curated seed terms categorized by technical domain
    SEED_TERMS: List[Tuple[str, int]] = [
        # System Design & Architecture
        ("system design interview", 9850),
        ("system design rate limiter", 8420),
        ("system design caching", 7650),
        ("system design distributed cache", 6910),
        ("system design url shortener", 6450),
        ("system design load balancer", 5900),
        ("system design consistent hashing", 5430),
        ("system design microservices", 5120),
        ("system design api gateway", 4890),
        ("system design cap theorem", 4600),
        ("system design message queues", 4320),
        ("system design database sharding", 4100),
        ("system design bloom filter", 3890),
        ("system design distributed locks", 3620),
        ("system design leader election", 3350),
        ("systemctl status", 3120),
        ("systemctl restart", 2890),
        ("system information mac", 2150),
        ("system monitor linux", 1980),
        ("systems architecture patterns", 1750),

        # Python Programming
        ("python tutorial", 9650),
        ("python list comprehension", 8720),
        ("python dictionary methods", 7890),
        ("python decorators", 7140),
        ("python async await", 6650),
        ("python virtual environment", 6100),
        ("python generator yield", 5620),
        ("python dataclasses", 5210),
        ("python type hints", 4870),
        ("python multiprocessing", 4540),
        ("python multithreading", 4230),
        ("python memory management", 3980),
        ("python lambda function", 3760),
        ("python packaging pip", 3420),
        ("python unit test unittest", 3150),
        ("python pytest fixtures", 2980),
        ("python fastapi tutorial", 2840),
        ("python pandas dataframe", 2710),
        ("python requests timeout", 2530),
        ("python string formatting", 2390),

        # React & Frontend
        ("react hooks guide", 9210),
        ("react useEffect dependency array", 8340),
        ("react useState functional update", 7720),
        ("react router dom v6", 6980),
        ("react native mobile", 6410),
        ("react server components", 5990),
        ("react context api", 5520),
        ("react performance memo", 5140),
        ("react custom hooks", 4810),
        ("react query tanstack", 4490),
        ("react redux toolkit", 4120),
        ("react lifecycle methods", 3820),
        ("react synthetic events", 3490),
        ("react typescript props", 3210),
        ("react nextjs ssr", 3050),
        ("react virtual dom", 2870),
        ("react forwardRef", 2640),
        ("react suspense streaming", 2430),
        ("react strict mode", 2210),
        ("react testing library", 2040),

        # Docker & Containers
        ("docker container run", 9120),
        ("docker compose up", 8540),
        ("docker build command", 7930),
        ("dockerfile best practices", 7350),
        ("docker multi stage build", 6780),
        ("docker volume mount", 6210),
        ("docker exec bash interactive", 5790),
        ("docker restart always", 5320),
        ("docker port mapping", 4950),
        ("docker system prune", 4630),
        ("docker network create", 4310),
        ("docker inspect container", 3970),
        ("docker logs tail follow", 3680),
        ("docker cp command", 3390),
        ("docker run detach", 3140),
        ("docker stop vs kill", 2920),
        ("docker healthcheck instruction", 2710),
        ("docker image tags", 2520),
        ("docker entrypoint vs cmd", 2380),
        ("docker registry push", 2190),

        # Redis & In-Memory Storage
        ("redis cache architecture", 8940),
        ("redis sorted set zset", 8120),
        ("redis pub sub messaging", 7450),
        ("redis eviction policies lru", 6830),
        ("redis cluster sharding", 6320),
        ("redis persistence rdb aof", 5810),
        ("redis string set get", 5410),
        ("redis hash hset hget", 5020),
        ("redis transactions multi exec", 4690),
        ("redis memory optimization", 4380),
        ("redis pipeline batching", 4090),
        ("redis sentinel high availability", 3780),
        ("redis key expiration ttl", 3510),
        ("redis distributed lock redlock", 3280),
        ("redis streams consumer groups", 3040),
        ("redis bitmap operations", 2810),
        ("redis hyperloglog cardinality", 2590),
        ("redis lua scripting eval", 2410),
        ("redis maxmemory policy", 2230),
        ("redis connection pool", 2070),

        # Databases & SQL
        ("sql indexing b tree", 8760),
        ("sql join inner left right", 8010),
        ("sql query optimization explain", 7390),
        ("sql transactions acid", 6740),
        ("sql foreign key constraint", 6180),
        ("sql group by having", 5690),
        ("sql subquery vs join", 5230),
        ("sql injection prevention", 4860),
        ("sql window functions rank", 4520),
        ("sql cte common table expression", 4210),
        ("postgresql explain analyze", 3920),
        ("postgresql jsonb indexing", 3650),
        ("postgresql connection pooling pgbouncer", 3410),
        ("postgresql partitioning declarative", 3190),
        ("postgresql wal write ahead log", 2980),
        ("mysql innodb buffer pool", 2790),
        ("mysql replication semi sync", 2580),
        ("sqlite in memory database", 2420),
        ("mongodb aggregation pipeline", 2250),
        ("database isolation levels", 2110),

        # Kubernetes & DevOps
        ("kubernetes pod lifecycle", 8650),
        ("kubernetes deployment strategy", 7920),
        ("kubernetes service clusterip nodeport", 7290),
        ("kubernetes ingress controller", 6680),
        ("kubernetes configmap secret", 6140),
        ("kubernetes horizontal pod autoscaler", 5630),
        ("kubernetes persistent volume claim", 5190),
        ("kubernetes helm chart values", 4790),
        ("kubernetes daemonset vs deployment", 4430),
        ("kubernetes statefulset headless service", 4110),
        ("kubernetes liveness readiness probe", 3840),
        ("kubernetes node affinity toleration", 3590),
        ("kubernetes network policy calico", 3340),
        ("kubernetes cert manager letsencrypt", 3110),
        ("kubernetes operator pattern crd", 2910),
        ("ci cd pipeline github actions", 2740),
        ("terraform state file management", 2560),
        ("ansible playbook automation", 2390),
        ("prometheus grafana monitoring", 2240),
        ("linux systemd service unit", 2090),

        # Go / Golang
        ("golang goroutine concurrency", 8510),
        ("golang channel buffer select", 7820),
        ("golang interface duck typing", 7190),
        ("golang error handling idiom", 6590),
        ("golang slice append allocation", 6070),
        ("golang context deadline cancellation", 5590),
        ("golang sync waitgroup mutex", 5160),
        ("golang pointer vs value receiver", 4780),
        ("golang struct tags json", 4410),
        ("golang defer execution order", 4090),
        ("golang memory escape analysis", 3810),
        ("golang garbage collector latency", 3550),
        ("golang test table driven", 3310),
        ("golang modules go mod init", 3090),
        ("golang http server routing", 2890),

        # Apache Kafka & Streaming
        ("kafka producer consumer architecture", 8430),
        ("kafka topic partition replication", 7740),
        ("kafka consumer lag offset commit", 7110),
        ("kafka broker cluster rebalance", 6520),
        ("kafka exactly once semantics eos", 5990),
        ("kafka log compaction cleanup", 5510),
        ("kafka schema registry avro", 5080),
        ("kafka connect source sink", 4690),
        ("kafka streams stateful processing", 4340),
        ("kafka kraft consensus controller", 4020),
    ]

    def __init__(self, queries: Dict[str, int] = None):
        """Initialize the dataset with either given queries or SEED_TERMS."""
        if queries is not None:
            self._queries = queries
        else:
            self._queries = {term: freq for term, freq in self.SEED_TERMS}

    @property
    def queries(self) -> Dict[str, int]:
        """Returns the dictionary mapping queries to frequency."""
        return self._queries

    def __len__(self) -> int:
        return len(self._queries)

    def get_test_prefixes(self) -> List[str]:
        """Returns standardized prefixes used across all milestone verification tests."""
        return ["sys", "py", "rea", "do", "red", "sql", "kub", "go", "kaf"]

    @classmethod
    def generate_expanded_corpus(cls, target_size: int = 10000, seed: int = 42) -> "QueryDataset":
        """
        Procedurally generates an expanded corpus following a Zipfian distribution.
        
        Zipf's law states that frequency is inversely proportional to its rank:
        freq(r) = C / (r ^ alpha), where alpha is close to 1.0.
        """
        random.seed(seed)
        generated: Dict[str, int] = {}

        # First add all curated seed queries
        for term, freq in cls.SEED_TERMS:
            generated[term] = freq

        # Vocabulary pools for procedural expansion
        tech_subjects = [
            "python", "system", "react", "docker", "redis", "kubernetes", "golang",
            "kafka", "postgres", "mysql", "mongodb", "aws", "gcp", "azure", "linux",
            "nginx", "graphql", "rest", "grpc", "node", "typescript", "rust", "java",
            "csharp", "elasticsearch", "cassandra", "dynamodb", "rabbitmq", "celery",
            "fastapi", "django", "flask", "spring", "express", "terraform", "ansible",
            "git", "bash", "prometheus", "grafana", "datadog", "sentry", "cloudflare",
        ]

        tech_verbs = [
            "tutorial", "guide", "best practices", "architecture", "interview questions",
            "configuration", "optimization", "performance tuning", "debugging",
            "error handling", "memory leak", "scaling", "concurrency", "rate limiting",
            "caching", "monitoring", "backup recovery", "deployment", "setup",
            "cluster setup", "failover", "sharding", "benchmarking", "load testing",
            "cheat sheet", "comparison", "migration", "security audit", "internals",
        ]

        tech_modifiers = [
            "for beginners", "in production", "with docker", "high availability",
            "step by step", "deep dive", "under high load", "zero downtime",
            "microservices", "distributed systems", "cloud native", "open source",
            "explained simply", "enterprise edition", "real world example",
        ]

        # Generate unique combinations until target_size is reached
        rank = len(generated) + 1
        base_constant = 100000.0  # Constant for Zipfian calculation

        while len(generated) < target_size:
            subj = random.choice(tech_subjects)
            verb = random.choice(tech_verbs)
            
            # 60% chance of 2-word query, 40% chance of 3-word query
            if random.random() < 0.4:
                mod = random.choice(tech_modifiers)
                query = f"{subj} {verb} {mod}"
            else:
                query = f"{subj} {verb}"

            if query not in generated:
                # Zipfian power-law decay: freq = max(1, int(C / rank^1.02))
                freq = max(1, int(base_constant / (math.pow(rank, 1.02))))
                # Add slight random jitter (0.85 to 1.15) to emulate natural search behavior
                jitter = random.uniform(0.85, 1.15)
                freq = max(1, int(freq * jitter))
                generated[query] = freq
                rank += 1

        return cls(generated)

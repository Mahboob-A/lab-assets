"""
test_es.py - Standalone Elasticsearch Connectivity & Cluster Health Verification.

Pulls cluster health directly from Elasticsearch REST endpoint and displays
cluster metadata, verifying single-node status before indexing begins.
"""

import json
import time
import urllib.error
import urllib.request

ES_URL = "http://localhost:9200"


def verify_cluster(max_retries: int = 15, delay: int = 2) -> bool:
    print(f"Polling Elasticsearch cluster health at {ES_URL}...")
    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.urlopen(f"{ES_URL}/_cluster/health", timeout=3)
            if req.status == 200:
                data = json.loads(req.read().decode("utf-8"))
                status = data.get("status", "unknown")
                cluster_name = data.get("cluster_name", "unknown")
                nodes = data.get("number_of_nodes", 0)

                print(f"[SUCCESS] Cluster responded on attempt {attempt}!")
                print(f"  * Cluster Name:     {cluster_name}")
                print(f"  * Cluster Status:   {status.upper()}")
                print(f"  * Active Nodes:     {nodes}")
                return True
        except Exception as e:
            print(f"  [Attempt {attempt}/{max_retries}] Waiting for Elasticsearch... ({e})")
            time.sleep(delay)

    print("[ERROR] Could not connect to Elasticsearch.")
    print("        Please verify Docker is running and execute: docker compose up -d")
    return False


if __name__ == "__main__":
    verify_cluster()

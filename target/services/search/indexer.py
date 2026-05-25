"""Elasticsearch bulk indexer for search-api.

BUG (tracked: SENT-1078): bulk_index sends the entire document list in a
single request with no batching, no rate limiting, and no backoff. Under
catalog rebuild (500k+ docs) this saturates ES write throughput, triggers
429 responses, and starves concurrent search reads.
"""
import time
from elasticsearch import Elasticsearch
import os

ES_URL = os.environ.get("ELASTICSEARCH_URL", "http://localhost:9200")
es = Elasticsearch(ES_URL)


def bulk_index(docs: list[dict], index: str = "products") -> dict:
    body = []
    for doc in docs:
        body.extend([
            {"index": {"_index": index, "_id": doc.get("id")}},
            doc,
        ])

    # BUG: sends ALL docs in one shot — no batching, no rate limiting
    # A 500k-doc rebuild generates a ~500 MB request body, hitting the
    # default 100 MB ES limit and overwhelming the write thread pool.
    response = es.bulk(body=body, timeout="30s")   # ← unbounded request size

    errors = [r for r in response.get("items", []) if r.get("index", {}).get("error")]
    return {"indexed": len(docs) - len(errors), "errors": len(errors)}


def index_single(doc: dict, index: str = "products") -> bool:
    resp = es.index(index=index, id=doc.get("id"), body=doc)
    return resp["result"] in ("created", "updated")

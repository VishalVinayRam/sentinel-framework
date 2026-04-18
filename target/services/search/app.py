"""Search API — handles product search and item lookup."""
import random
import sys
import time
import uuid
from pathlib import Path

from flask import Flask, jsonify, request
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.log_shipper import LogShipper

app = Flask(__name__)
log = LogShipper("search-api")

REQUESTS = Counter("search_requests_total",  "Total requests", ["method", "endpoint", "status"])
LATENCY  = Histogram("search_latency_seconds", "Request latency", ["endpoint"])
ERRORS   = Counter("search_errors_total",    "Total errors")
QUERIES  = Counter("search_queries_total",   "Search queries executed")

_chaos = {"mode": None, "error_rate": 0.0, "latency_ms": 0}

ITEMS = [
    {"id": f"item_{i}", "name": f"Product {i}", "price": round(random.uniform(5, 500), 2)}
    for i in range(1, 50)
]


def _apply_chaos():
    if _chaos["latency_ms"]:
        time.sleep(_chaos["latency_ms"] / 1000)
    if _chaos["error_rate"] and random.random() < _chaos["error_rate"]:
        msg = {
            "P1": "FATAL: Elasticsearch cluster unreachable — all nodes down",
            "P2": "ERROR: Search index rebuild job consuming all write threads, reads timing out",
            "P3": "WARN: Elasticsearch 429 — bulk indexing rate limit hit",
        }.get(_chaos["mode"], "ERROR: Search service degraded")
        log.error(msg, chaos_mode=_chaos["mode"])
        return True, 500
    return False, 200


@app.before_request
def _track():
    request._start = time.time()


@app.after_request
def _record(resp):
    duration = time.time() - getattr(request, "_start", time.time())
    REQUESTS.labels(request.method, request.path, resp.status_code).inc()
    LATENCY.labels(request.path).observe(duration)
    if resp.status_code >= 500:
        ERRORS.inc()
    return resp


@app.get("/health")
def health():
    if _chaos["mode"] == "P1":
        log.fatal("Health check FAILED — Elasticsearch unreachable")
        return jsonify({"status": "unhealthy", "chaos": _chaos["mode"]}), 500
    if _chaos["mode"] == "P2" and random.random() < 0.4:
        return jsonify({"status": "degraded", "chaos": _chaos["mode"]}), 500
    return jsonify({"status": "ok", "service": "search-api"})


@app.get("/search")
def search():
    failed, code = _apply_chaos()
    if failed:
        return jsonify({"error": "Search temporarily unavailable"}), code
    q = request.args.get("q", "")
    results = [i for i in ITEMS if q.lower() in i["name"].lower()] if q else ITEMS[:10]
    QUERIES.inc()
    log.info("Search executed", query=q, results=len(results))
    return jsonify({"query": q, "results": results, "total": len(results)})


@app.get("/items/<item_id>")
def get_item(item_id):
    failed, code = _apply_chaos()
    if failed:
        return jsonify({"error": "Item lookup failed — index unavailable"}), code
    item = next((i for i in ITEMS if i["id"] == item_id), None)
    if not item:
        return jsonify({"error": "Item not found"}), 404
    log.info("Item fetched", item_id=item_id)
    return jsonify(item)


@app.post("/fault")
def set_fault():
    data = request.get_json() or {}
    _chaos.update({
        "mode":       data.get("mode"),
        "error_rate": data.get("error_rate", 0.0),
        "latency_ms": data.get("latency_ms", 0),
    })
    if _chaos["mode"]:
        log.error(f"CHAOS INJECTED: {_chaos['mode']} — error_rate={_chaos['error_rate']}", **_chaos)
    else:
        log.info("Chaos cleared — search-api restored")
    return jsonify({"ok": True, "chaos": _chaos})


@app.get("/metrics")
def metrics():
    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}


if __name__ == "__main__":
    log.info("search-api starting", port=5003)
    app.run(host="0.0.0.0", port=5003)

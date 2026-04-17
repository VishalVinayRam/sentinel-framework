"""Payments service — handles charges, refunds, balance checks."""
import random
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.log_shipper import LogShipper

app = Flask(__name__)
log = LogShipper("payments-service")

REQUESTS = Counter("payments_requests_total",  "Total requests", ["method", "endpoint", "status"])
LATENCY  = Histogram("payments_latency_seconds", "Request latency", ["endpoint"])
ERRORS   = Counter("payments_errors_total",   "Total errors")
CHARGED  = Counter("payments_charged_total",  "Successful charges")

_chaos = {"mode": None, "error_rate": 0.0, "latency_ms": 0}


def _apply_chaos():
    if _chaos["latency_ms"]:
        time.sleep(_chaos["latency_ms"] / 1000)
    if _chaos["error_rate"] and random.random() < _chaos["error_rate"]:
        msg = {
            "P1": "FATAL: Payment gateway connection refused — all retries exhausted",
            "P2": "ERROR: Memory leak in charge processor — heap at 94%, GC pausing 800ms",
            "P3": "WARN: Stripe API 429 — rate limit exceeded, backing off",
        }.get(_chaos["mode"], "ERROR: Payments service degraded")
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
        log.fatal("Health check FAILED — payments gateway unreachable")
        return jsonify({"status": "unhealthy", "chaos": _chaos["mode"]}), 500
    if _chaos["mode"] == "P2" and random.random() < 0.4:
        return jsonify({"status": "degraded", "chaos": _chaos["mode"]}), 500
    return jsonify({"status": "ok", "service": "payments-service"})


@app.post("/charge")
def charge():
    failed, code = _apply_chaos()
    if failed:
        return jsonify({"error": "Payment processing failed — try again"}), code
    data = request.get_json(silent=True) or {}
    amount = data.get("amount", 0)
    charge_id = f"ch_{uuid.uuid4().hex[:16]}"
    CHARGED.inc()
    log.info("Charge processed", charge_id=charge_id, amount=amount)
    return jsonify({"charge_id": charge_id, "amount": amount, "status": "succeeded"})


@app.get("/balance/<user_id>")
def balance(user_id):
    failed, code = _apply_chaos()
    if failed:
        return jsonify({"error": "Balance unavailable"}), code
    log.info("Balance fetched", user_id=user_id)
    return jsonify({"user_id": user_id, "balance": round(random.uniform(10, 5000), 2), "currency": "USD"})


@app.post("/refund")
def refund():
    failed, code = _apply_chaos()
    if failed:
        return jsonify({"error": "Refund processing failed"}), code
    data = request.get_json(silent=True) or {}
    refund_id = f"re_{uuid.uuid4().hex[:16]}"
    log.info("Refund issued", refund_id=refund_id, charge_id=data.get("charge_id"))
    return jsonify({"refund_id": refund_id, "status": "pending"})


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
        log.info("Chaos cleared — payments-service restored")
    return jsonify({"ok": True, "chaos": _chaos})


@app.get("/metrics")
def metrics():
    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}


if __name__ == "__main__":
    log.info("payments-service starting", port=5002)
    app.run(host="0.0.0.0", port=5002)

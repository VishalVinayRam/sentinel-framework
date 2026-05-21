"""Auth service — handles login, logout, token refresh."""
import random
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import boto3
from flask import Flask, jsonify, request
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.log_shipper import LogShipper

app = Flask(__name__)
log = LogShipper("auth-service")

FLOCI = dict(endpoint_url="http://localhost:4566", region_name="us-east-1",
             aws_access_key_id="test", aws_secret_access_key="test")

# Prometheus metrics
REQUESTS  = Counter("auth_requests_total",  "Total requests", ["method", "endpoint", "status"])
LATENCY   = Histogram("auth_latency_seconds", "Request latency", ["endpoint"])
ERRORS    = Counter("auth_errors_total", "Total errors")
ACTIVE    = Counter("auth_active_sessions_total", "Sessions created")

# Chaos state — mutated by /fault endpoint
_chaos = {"mode": None, "error_rate": 0.0, "latency_ms": 0}


def _apply_chaos():
    """Return (should_fail, status_code) based on current chaos mode."""
    if _chaos["latency_ms"]:
        time.sleep(_chaos["latency_ms"] / 1000)
    if _chaos["error_rate"] and random.random() < _chaos["error_rate"]:
        msg = {
            "P1": "FATAL: Database connection pool exhausted — all 50 connections saturated",
            "P2": "ERROR: Token cache OOM — LRU eviction not firing, memory at 1.8 GB",
            "P3": "WARN: Upstream rate limit hit — 429 from identity provider",
        }.get(_chaos["mode"], "ERROR: Service degraded")
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
        log.fatal("Health check failed — service in P1 chaos mode")
        return jsonify({"status": "unhealthy", "chaos": _chaos["mode"]}), 500
    if _chaos["mode"] == "P2" and random.random() < 0.4:
        return jsonify({"status": "degraded", "chaos": _chaos["mode"]}), 500
    return jsonify({"status": "ok", "service": "auth-service"})


@app.post("/login")
def login():
    failed, code = _apply_chaos()
    if failed:
        return jsonify({"error": "Authentication service unavailable"}), code
    data = request.get_json(silent=True) or {}
    token = str(uuid.uuid4())
    ACTIVE.inc()
    log.info("User logged in", user=data.get("username", "unknown"), token_prefix=token[:8])
    return jsonify({"token": token, "expires_in": 3600})


@app.post("/logout")
def logout():
    failed, code = _apply_chaos()
    if failed:
        return jsonify({"error": "Auth service unavailable"}), code
    log.info("User logged out")
    return jsonify({"ok": True})


@app.get("/profile/<user_id>")
def profile(user_id):
    failed, code = _apply_chaos()
    if failed:
        return jsonify({"error": "Cannot fetch profile — database unreachable"}), code
    log.info("Profile fetched", user_id=user_id)
    return jsonify({"user_id": user_id, "name": f"User {user_id[:6]}", "email": f"{user_id[:6]}@example.com"})


@app.post("/token/refresh")
def refresh():
    failed, code = _apply_chaos()
    if failed:
        return jsonify({"error": "Token refresh failed"}), code
    return jsonify({"token": str(uuid.uuid4()), "expires_in": 3600})


@app.post("/fault")
def set_fault():
    """Chaos endpoint — called by chaos_monkey.py to inject failures."""
    data = request.get_json() or {}
    _chaos.update({
        "mode":       data.get("mode"),
        "error_rate": data.get("error_rate", 0.0),
        "latency_ms": data.get("latency_ms", 0),
    })
    if _chaos["mode"]:
        log.error(f"CHAOS INJECTED: {_chaos['mode']} mode — error_rate={_chaos['error_rate']}", **_chaos)
    else:
        log.info("Chaos cleared — service restored to normal")
    return jsonify({"ok": True, "chaos": _chaos})


@app.get("/metrics")
def metrics():
    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}


if __name__ == "__main__":
    log.info("auth-service starting", port=5001)
    app.run(host="0.0.0.0", port=5001)

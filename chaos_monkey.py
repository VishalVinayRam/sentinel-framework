#!/usr/bin/env python3
"""
Chaos Monkey for the Sentinel demo.

Injects realistic P1/P2/P3 failures into the target microservices and
fires alerts directly onto the Kinesis sentinel-alerts stream so the
Sentinel pipeline picks them up.

Usage:
  python3 chaos_monkey.py                              # auto mode (random chaos every 90s)
  python3 chaos_monkey.py --service auth-service --severity P2
  python3 chaos_monkey.py --heal                       # restore all services
"""
import argparse
import json
import os
import random
import sys
import time
import uuid
from datetime import datetime, timezone

import boto3
import requests

# ── Config ────────────────────────────────────────────────────────────────────

SERVICES = {
    "auth-service":     "http://localhost:5001",
    "payments-service": "http://localhost:5002",
    "search-api":       "http://localhost:5003",
}

CHAOS_PROFILES = {
    "P1": {
        "label":       "CRITICAL OUTAGE",
        "error_rate":  0.95,
        "latency_ms":  0,
        "color":       "\033[91m",  # bright red
        "descriptions": {
            "auth-service":     "Database connection pool exhausted — all 50 connections saturated",
            "payments-service": "Payment gateway connection refused — all retries exhausted",
            "search-api":       "Elasticsearch cluster unreachable — all nodes down",
        },
    },
    "P2": {
        "label":       "MAJOR DEGRADATION",
        "error_rate":  0.40,
        "latency_ms":  2000,
        "color":       "\033[93m",  # yellow
        "descriptions": {
            "auth-service":     "Memory leak in token cache — LRU eviction not firing, heap at 94%",
            "payments-service": "GC pauses every 800ms — charge processor heap growing unbounded",
            "search-api":       "Index rebuild job consuming all write threads, read latency 3400ms",
        },
    },
    "P3": {
        "label":       "ELEVATED ERROR RATE",
        "error_rate":  0.15,
        "latency_ms":  500,
        "color":       "\033[33m",  # orange
        "descriptions": {
            "auth-service":     "Identity provider rate-limiting — 429s on token refresh requests",
            "payments-service": "Stripe API rate limit — batch job sending without backoff",
            "search-api":       "Elasticsearch 429 — bulk indexing quota exceeded",
        },
    },
}

FLOCI = dict(
    endpoint_url="http://localhost:4566",
    region_name="us-east-1",
    aws_access_key_id="test",
    aws_secret_access_key="test",
)

R = "\033[0m"
G = "\033[92m"
B = "\033[96m"
BOLD = "\033[1m"

# Track active chaos: {service_name: {"severity", "since", "incident_id"}}
_active: dict[str, dict] = {}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _kinesis():
    return boto3.client("kinesis", **FLOCI)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _svc_status(name: str) -> str:
    try:
        r = requests.get(f"{SERVICES[name]}/health", timeout=2)
        if r.status_code == 200:
            return f"{G}●{R} healthy"
        return f"\033[91m●{R} unhealthy ({r.status_code})"
    except Exception:
        return f"\033[90m○{R} unreachable"


def _inject(service: str, severity: str) -> str:
    """Inject chaos into a service and fire a Sentinel alert."""
    profile = CHAOS_PROFILES[severity]
    iid = str(uuid.uuid4())

    # 1. Set fault mode on the service
    try:
        requests.post(
            f"{SERVICES[service]}/fault",
            json={"mode": severity, "error_rate": profile["error_rate"], "latency_ms": profile["latency_ms"]},
            timeout=3,
        )
    except Exception as e:
        print(f"  {_ts()} Could not reach {service}: {e}", flush=True)
        return iid

    # 2. Fire alert to Kinesis sentinel-alerts (same format validator expects)
    alert = {
        "alert_id":        iid,
        "service_name":    service,
        "severity":        severity,
        "error_rate":      profile["error_rate"],
        "p99_latency_ms":  profile["latency_ms"] or 50,
        "health_endpoint": f"{SERVICES[service]}/health",
        "source":          "chaos-monkey",
        "description":     profile["descriptions"].get(service, "Service degraded"),
        "fired_at":        _now(),
        "labels":          {"service": service, "severity": severity.lower()},
    }
    try:
        _kinesis().put_record(
            StreamName="sentinel-alerts",
            Data=json.dumps(alert),
            PartitionKey=service,
        )
        print(f"  {_ts()} Alert fired to Kinesis: {iid[:8]}… ({severity}/{service})", flush=True)
    except Exception as e:
        print(f"  {_ts()} Kinesis unavailable — {e}", flush=True)

    _active[service] = {"severity": severity, "since": time.time(), "incident_id": iid}
    return iid


def _heal(service: str):
    """Remove chaos from a service."""
    try:
        requests.post(f"{SERVICES[service]}/fault", json={"mode": None, "error_rate": 0, "latency_ms": 0}, timeout=3)
    except Exception:
        pass
    _active.pop(service, None)


def _heal_all():
    for svc in list(_active):
        _heal(svc)
    print(f"  {_ts()} All services restored", flush=True)


def _ts() -> str:
    return f"\033[90m{datetime.now().strftime('%H:%M:%S')}{R}"


def _elapsed(since: float) -> str:
    s = int(time.time() - since)
    return f"{s // 60}m {s % 60}s"


# ── Display ───────────────────────────────────────────────────────────────────

def _print_status():
    print(f"\n{BOLD}{B}{'═' * 62}{R}")
    print(f"{BOLD}{B}  SENTINEL CHAOS MONKEY   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{R}")
    print(f"{BOLD}{B}{'═' * 62}{R}\n")

    print(f"  {BOLD}Target services:{R}")
    for name in SERVICES:
        status = _svc_status(name)
        chaos = _active.get(name)
        if chaos:
            p = CHAOS_PROFILES[chaos["severity"]]
            elapsed = _elapsed(chaos["since"])
            print(f"    {p['color']}{chaos['severity']}{R}  {name:22s}  {status}  [{elapsed} in chaos]")
        else:
            print(f"    {G}OK {R}  {name:22s}  {status}")
    print()

    if _active:
        print(f"  {BOLD}Active chaos:{R}")
        for svc, info in _active.items():
            p = CHAOS_PROFILES[info["severity"]]
            desc = p["descriptions"].get(svc, "")
            print(f"    {p['color']}{info['severity']}{R}  {svc}")
            print(f"         {desc}")
            print(f"         error_rate={p['error_rate']:.0%}  latency={p['latency_ms']}ms  "
                  f"incident={info['incident_id'][:8]}…")
        print()

    sentinel_ok = _check_sentinel()
    print(f"  {BOLD}Sentinel:{R}")
    if sentinel_ok:
        try:
            r = requests.get("http://localhost:8501/api/stats", timeout=2).json()
            print(f"    {G}●{R} Dashboard running — {r.get('open_incidents',0)} open incidents")
        except Exception:
            print(f"    {G}●{R} Reachable")
    else:
        print(f"    \033[90m○{R} Not running — start with ./setup_demo.sh")
    print()


def _check_sentinel() -> bool:
    try:
        return requests.get("http://localhost:8501/health", timeout=2).status_code == 200
    except Exception:
        return False


# ── Modes ─────────────────────────────────────────────────────────────────────

def manual_mode(service: str, severity: str):
    print(f"\n{_ts()} Injecting {CHAOS_PROFILES[severity]['label']} into {service}…")
    iid = _inject(service, severity)
    print(f"{_ts()} Done. incident_id={iid}")
    print(f"{_ts()} Sentinel pipeline will process this in ~10s if sentinel_pipeline.py is running.")
    print(f"{_ts()} Dashboard: http://localhost:8501")
    print(f"\nTo restore: python3 chaos_monkey.py --heal\n")


def heal_mode():
    print(f"\n{_ts()} Restoring all services…")
    _heal_all()
    print(f"{_ts()} Done.\n")


def auto_mode():
    """Randomly inject and heal chaos, cycling through services."""
    print(f"\n{BOLD}{B}Auto chaos mode{R} — Ctrl+C to stop\n")
    print(f"  Services:  {', '.join(SERVICES)}")
    print(f"  Sentinel:  http://localhost:8501")
    print(f"  Grafana:   http://localhost:3000  (admin / sentinel)")
    print(f"  Prometheus:http://localhost:9090\n")

    while True:
        _print_status()

        # Heal any service that has been in chaos for > 90 seconds
        for svc, info in list(_active.items()):
            if time.time() - info["since"] > 90:
                print(f"  {_ts()} Healing {svc} after 90s…")
                _heal(svc)

        # Inject new chaos if fewer than 1 service is currently affected
        if len(_active) < 1:
            svc = random.choice(list(SERVICES))
            sev = random.choices(["P1", "P2", "P3"], weights=[1, 2, 3])[0]
            print(f"  {_ts()} Injecting {CHAOS_PROFILES[sev]['label']} into {svc}…")
            _inject(svc, sev)

        time.sleep(15)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Sentinel Chaos Monkey")
    parser.add_argument("--service", choices=list(SERVICES), help="Target service")
    parser.add_argument("--severity", choices=["P1", "P2", "P3"], help="Chaos severity")
    parser.add_argument("--heal", action="store_true", help="Restore all services")
    args = parser.parse_args()

    if args.heal:
        heal_mode()
    elif args.service and args.severity:
        manual_mode(args.service, args.severity)
    elif args.service or args.severity:
        parser.error("--service and --severity must be used together")
    else:
        auto_mode()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n{_ts()} Interrupted — healing all services…")
        _heal_all()
        sys.exit(0)

#!/usr/bin/env python3
"""
Chaos Monkey for the Sentinel demo — enhanced live-action edition.

Injects realistic P1/P2/P3 failures into the target microservices, fires
alerts onto the Kinesis sentinel-alerts stream, simulates real traffic, and
shows a live terminal dashboard that updates every second.

Usage:
  python3 chaos_monkey.py                          # live auto-chaos dashboard
  python3 chaos_monkey.py --demo                   # scripted demo sequence
  python3 chaos_monkey.py --service auth-service --severity P2
  python3 chaos_monkey.py --heal                   # restore all services
"""
import argparse
import collections
import json
import os
import random
import sys
import threading
import time
import uuid
from datetime import datetime, timezone

import boto3
import requests

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.progress import Progress, BarColumn, TextColumn, TimeElapsedColumn
from rich import box

# ── Config ────────────────────────────────────────────────────────────────────

SERVICES = {
    "auth-service":     {"url": "http://localhost:5001", "endpoints": [
        ("GET",  "/health",         {}),
        ("POST", "/login",          {"username": "user@corp.io", "password": "secret"}),
        ("POST", "/logout",         {"token": "demo-token"}),
    ]},
    "payments-service": {"url": "http://localhost:5002", "endpoints": [
        ("GET",  "/health",         {}),
        ("POST", "/charge",         {"amount": 9.99, "card": "4242424242424242"}),
        ("POST", "/refund",         {"charge_id": "ch_demo"}),
    ]},
    "search-api":       {"url": "http://localhost:5003", "endpoints": [
        ("GET",  "/health",         {}),
        ("GET",  "/search",         {"q": "product", "page": 1}),
        ("GET",  "/items/demo-123", {}),
    ]},
}

CHAOS_PROFILES = {
    "P1": {
        "label":       "CRITICAL OUTAGE",
        "error_rate":  0.95,
        "latency_ms":  0,
        "color":       "bold red",
        "heal_after":  90,
        "descriptions": {
            "auth-service":     "DB connection pool exhausted — all 50 connections saturated",
            "payments-service": "Payment gateway refused — all retries exhausted",
            "search-api":       "Elasticsearch cluster unreachable — all nodes down",
        },
    },
    "P2": {
        "label":       "MAJOR DEGRADATION",
        "error_rate":  0.40,
        "latency_ms":  2000,
        "color":       "bold yellow",
        "heal_after":  75,
        "descriptions": {
            "auth-service":     "Memory leak in token cache — heap at 94%, LRU eviction stalled",
            "payments-service": "GC pauses every 800ms — charge processor OOM loop",
            "search-api":       "Index rebuild consuming all write threads, p99=3400ms",
        },
    },
    "P3": {
        "label":       "ELEVATED ERRORS",
        "error_rate":  0.15,
        "latency_ms":  500,
        "color":       "yellow",
        "heal_after":  60,
        "descriptions": {
            "auth-service":     "Identity provider rate-limiting — 429s on token refresh",
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

SENTINEL_URL = "http://localhost:8501"

# ── Shared state (thread-safe via GIL + deque/dict) ──────────────────────────

_active: dict[str, dict] = {}          # service → {severity, since, incident_id}
_metrics: dict[str, dict] = {          # service → rolling counters
    svc: {"ok": 0, "err": 0, "latencies": collections.deque(maxlen=60)}
    for svc in SERVICES
}
_event_log: collections.deque = collections.deque(maxlen=30)
_sentinel_stats: dict = {}
_stop = threading.Event()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _kinesis():
    return boto3.client("kinesis", **FLOCI)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _elapsed(since: float) -> str:
    s = int(time.time() - since)
    return f"{s // 60}m {s % 60:02d}s"


def _log(msg: str, color: str = "white"):
    _event_log.appendleft(f"[{_ts()}] [{color}]{msg}[/{color}]")


# ── Traffic simulator (background thread) ─────────────────────────────────────

def _traffic_worker():
    """Continuously send requests to all services and record live metrics."""
    session = requests.Session()
    svc_names = list(SERVICES)
    while not _stop.is_set():
        svc = random.choice(svc_names)
        cfg = SERVICES[svc]
        method, path, body = random.choice(cfg["endpoints"])
        url = cfg["url"] + path
        try:
            t0 = time.monotonic()
            if method == "GET":
                r = session.get(url, params=body, timeout=4)
            else:
                r = session.post(url, json=body, timeout=4)
            lat = (time.monotonic() - t0) * 1000
            _metrics[svc]["latencies"].append(lat)
            if r.status_code >= 500:
                _metrics[svc]["err"] += 1
            else:
                _metrics[svc]["ok"] += 1
        except requests.exceptions.ConnectionError:
            pass
        except Exception:
            _metrics[svc]["err"] += 1
        time.sleep(0.2)


# ── Sentinel poller (background thread) ──────────────────────────────────────

def _sentinel_worker():
    while not _stop.is_set():
        try:
            r = requests.get(f"{SENTINEL_URL}/api/stats", timeout=2)
            if r.status_code == 200:
                _sentinel_stats.update(r.json())
        except Exception:
            pass
        time.sleep(5)


# ── Chaos injection / healing ─────────────────────────────────────────────────

def _inject(service: str, severity: str) -> str:
    profile = CHAOS_PROFILES[severity]
    iid = str(uuid.uuid4())

    try:
        requests.post(
            SERVICES[service]["url"] + "/fault",
            json={"mode": severity, "error_rate": profile["error_rate"], "latency_ms": profile["latency_ms"]},
            timeout=3,
        )
    except Exception as e:
        _log(f"Could not reach {service}: {e}", "red")
        return iid

    alert = {
        "alert_id":        iid,
        "service_name":    service,
        "severity":        severity,
        "error_rate":      profile["error_rate"],
        "p99_latency_ms":  profile["latency_ms"] or 50,
        "health_endpoint": SERVICES[service]["url"] + "/health",
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
        _log(f"Alert → Kinesis [{severity}] {service} | id={iid[:8]}…", "cyan")
    except Exception as e:
        _log(f"Kinesis unavailable: {e}", "red")

    _active[service] = {"severity": severity, "since": time.time(), "incident_id": iid}
    _log(f"Chaos injected: [{severity}] {service} — {profile['descriptions'].get(service,'')}", profile["color"])
    return iid


def _heal(service: str):
    try:
        requests.post(
            SERVICES[service]["url"] + "/fault",
            json={"mode": None, "error_rate": 0, "latency_ms": 0},
            timeout=3,
        )
        _log(f"Healed {service} — service restored", "green")
    except Exception:
        pass
    _active.pop(service, None)


def _heal_all():
    for svc in list(_active):
        _heal(svc)


# ── Rich UI builders ──────────────────────────────────────────────────────────

def _svc_color(name: str) -> str:
    if name in _active:
        return CHAOS_PROFILES[_active[name]["severity"]]["color"]
    return "green"


def _build_services_panel() -> Panel:
    t = Table(box=box.SIMPLE_HEAD, show_header=True, expand=True, pad_edge=False)
    t.add_column("Service",    style="bold", width=20)
    t.add_column("Status",     width=14)
    t.add_column("Req/s",      width=8, justify="right")
    t.add_column("Err %",      width=8, justify="right")
    t.add_column("p99 ms",     width=9, justify="right")
    t.add_column("Chaos",      width=24)

    for svc in SERVICES:
        m      = _metrics[svc]
        total  = m["ok"] + m["err"]
        rps    = f"{total / 60:.1f}" if total else "—"
        err_pct = f"{m['err']/total*100:.1f}%" if total else "—"
        lats   = list(m["latencies"])
        p99    = f"{sorted(lats)[int(len(lats)*0.99)]:.0f}" if lats else "—"

        if svc in _active:
            info = _active[svc]
            p    = CHAOS_PROFILES[info["severity"]]
            status_txt = Text("● CHAOS", style=p["color"])
            chaos_txt  = Text(f"{info['severity']} · {_elapsed(info['since'])}", style=p["color"])
        else:
            status_txt = Text("● healthy", style="green")
            chaos_txt  = Text("—", style="dim")

        err_style = "red" if total and m["err"]/total > 0.1 else ("yellow" if total and m["err"]/total > 0.02 else "")
        t.add_row(svc, status_txt, rps, Text(err_pct, style=err_style), p99, chaos_txt)

    return Panel(t, title="[bold]Target Services[/bold]", border_style="bright_black")


def _build_sentinel_panel() -> Panel:
    if not _sentinel_stats:
        return Panel("[dim]Sentinel not reachable — start with ./setup_demo.sh[/dim]",
                     title="[bold]Sentinel[/bold]", border_style="bright_black")
    open_i = _sentinel_stats.get("open_incidents", 0)
    p1p2   = (_sentinel_stats.get("severity_breakdown", {}).get("P1", 0) +
               _sentinel_stats.get("severity_breakdown", {}).get("P2", 0))
    fp     = _sentinel_stats.get("false_positive_rate", 0)
    mttr   = _sentinel_stats.get("mttr_minutes", 0)

    t = Table(box=box.SIMPLE, show_header=False, expand=True, pad_edge=False)
    t.add_column("k", style="dim", width=22)
    t.add_column("v", style="bold")
    t.add_row("Open incidents",     f"[{'red' if open_i else 'green'}]{open_i}[/]")
    t.add_row("Critical (P1/P2)",   f"[{'red bold' if p1p2 else 'dim'}]{p1p2}[/]")
    t.add_row("False positive rate",f"[yellow]{fp}%[/yellow]")
    t.add_row("Avg MTTR",           f"[cyan]{mttr}m[/cyan]" if mttr else "[dim]—[/dim]")
    t.add_row("Dashboard",          f"[link={SENTINEL_URL}]{SENTINEL_URL}[/link]")
    return Panel(t, title="[bold]Sentinel[/bold]", border_style="bright_black")


def _build_active_panel() -> Panel:
    if not _active:
        return Panel("[dim]No chaos active — all services healthy[/dim]",
                     title="[bold]Active Chaos[/bold]", border_style="green")
    t = Table(box=box.SIMPLE, show_header=False, expand=True, pad_edge=False)
    t.add_column("svc", width=22)
    t.add_column("info")
    for svc, info in _active.items():
        p    = CHAOS_PROFILES[info["severity"]]
        desc = p["descriptions"].get(svc, "")
        heal_in = max(0, p["heal_after"] - int(time.time() - info["since"]))
        t.add_row(
            Text(f"{info['severity']}  {svc}", style=p["color"]),
            Text(f"{desc}\n    ↳ heals in {heal_in}s · incident={info['incident_id'][:8]}…", style="dim"),
        )
    return Panel(t, title="[bold red]Active Chaos[/bold red]", border_style="red")


def _build_log_panel() -> Panel:
    lines = list(_event_log)[:14]
    txt = "\n".join(lines) if lines else "[dim]Waiting for events…[/dim]"
    return Panel(txt, title="[bold]Event Log[/bold]", border_style="bright_black")


def _build_header() -> Text:
    now  = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
    active_count = len(_active)
    color = "red bold" if active_count else "green bold"
    status = f"{active_count} service(s) in chaos" if active_count else "all services healthy"
    t = Text()
    t.append("  SENTINEL CHAOS MONKEY", style="bold white")
    t.append(f"  ·  {now}", style="dim")
    t.append(f"  ·  {status}", style=color)
    return t


def _build_layout() -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header",   size=3),
        Layout(name="services", size=10),
        Layout(name="middle",   size=12),
        Layout(name="log",      size=16),
    )
    layout["middle"].split_row(
        Layout(name="active",   ratio=3),
        Layout(name="sentinel", ratio=2),
    )
    layout["header"].update(Panel(_build_header(), border_style="bright_black", padding=(0, 1)))
    layout["services"].update(_build_services_panel())
    layout["active"].update(_build_active_panel())
    layout["sentinel"].update(_build_sentinel_panel())
    layout["log"].update(_build_log_panel())
    return layout


# ── Modes ─────────────────────────────────────────────────────────────────────

def auto_mode():
    """Live dashboard with random chaos injection and auto-healing."""
    console = Console()
    console.clear()

    # Start background threads
    threading.Thread(target=_traffic_worker, daemon=True).start()
    threading.Thread(target=_sentinel_worker, daemon=True).start()

    _log("Chaos monkey started — traffic simulation active", "cyan")
    _log(f"Services: {', '.join(SERVICES)}", "dim")

    with Live(_build_layout(), console=console, refresh_per_second=2, screen=True) as live:
        try:
            while True:
                # Auto-heal expired chaos
                for svc, info in list(_active.items()):
                    sev = info["severity"]
                    if time.time() - info["since"] > CHAOS_PROFILES[sev]["heal_after"]:
                        _heal(svc)

                # Inject new chaos if nothing active
                if not _active:
                    svc = random.choice(list(SERVICES))
                    sev = random.choices(["P1", "P2", "P3"], weights=[1, 2, 3])[0]
                    _inject(svc, sev)

                live.update(_build_layout())
                time.sleep(1)

        except KeyboardInterrupt:
            _stop.set()
            _heal_all()
            console.print("\n[green]All services healed. Goodbye.[/green]")


def demo_mode():
    """Scripted 3-minute demo sequence showing the full Sentinel pipeline."""
    console = Console()
    console.clear()

    threading.Thread(target=_traffic_worker, daemon=True).start()
    threading.Thread(target=_sentinel_worker, daemon=True).start()

    script = [
        (5,  lambda: _log("Demo starting — watching for anomalies…", "cyan")),
        (8,  lambda: _inject("auth-service", "P3")),
        (20, lambda: _log("Sentinel received P3 alert — running validation…", "cyan")),
        (30, lambda: _inject("auth-service", "P1")),
        (32, lambda: _log("P1 escalation! Sentinel pipeline triggered — AI RCA generating…", "red")),
        (45, lambda: _inject("payments-service", "P2")),
        (46, lambda: _log("Cascade: payments-service now degraded alongside auth-service", "yellow")),
        (60, lambda: _log("Sentinel AI RCA complete — check dashboard", "green")),
        (80, lambda: _heal("auth-service")),
        (90, lambda: _inject("search-api", "P3")),
        (110, lambda: _heal("payments-service")),
        (120, lambda: _log("Recovery underway — auth-service restored", "green")),
        (140, lambda: _heal("search-api")),
        (150, lambda: _log("All services recovered — MTTR tracking updated in Sentinel", "green")),
    ]

    _log("Demo mode: 2.5 minute scripted chaos sequence", "bold cyan")
    _log(f"Dashboard: {SENTINEL_URL}", "dim")

    start = time.time()
    fired = set()

    with Live(_build_layout(), console=console, refresh_per_second=2, screen=True) as live:
        try:
            while True:
                elapsed = time.time() - start
                for t, fn in script:
                    if t not in fired and elapsed >= t:
                        fn()
                        fired.add(t)

                live.update(_build_layout())
                if elapsed > 160:
                    break
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            _stop.set()
            _heal_all()
            console.print("\n[green]Demo complete. All services healed.[/green]")


def manual_mode(service: str, severity: str):
    console = Console()
    threading.Thread(target=_traffic_worker, daemon=True).start()
    threading.Thread(target=_sentinel_worker, daemon=True).start()

    _log(f"Injecting {CHAOS_PROFILES[severity]['label']} into {service}…", "cyan")
    iid = _inject(service, severity)
    _log(f"Done. incident_id={iid}", "green")
    _log(f"Dashboard: {SENTINEL_URL}", "dim")

    with Live(_build_layout(), console=console, refresh_per_second=2, screen=True) as live:
        try:
            while True:
                live.update(_build_layout())
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            _stop.set()
            _heal_all()
            console.print("\n[green]Healed. Goodbye.[/green]")


def heal_mode():
    console = Console()
    console.print(f"[dim][{_ts()}][/dim] Restoring all services…")
    _heal_all()
    console.print(f"[dim][{_ts()}][/dim] [green]Done.[/green]")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Sentinel Chaos Monkey — live edition")
    parser.add_argument("--service",  choices=list(SERVICES), help="Target service")
    parser.add_argument("--severity", choices=["P1", "P2", "P3"], help="Chaos severity")
    parser.add_argument("--heal",     action="store_true", help="Restore all services")
    parser.add_argument("--demo",     action="store_true", help="Run 2.5-min scripted demo sequence")
    args = parser.parse_args()

    if args.heal:
        heal_mode()
    elif args.demo:
        demo_mode()
    elif args.service and args.severity:
        manual_mode(args.service, args.severity)
    elif args.service or args.severity:
        parser.error("--service and --severity must be used together")
    else:
        auto_mode()


if __name__ == "__main__":
    main()

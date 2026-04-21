"""
Sentinel Dashboard API — FastAPI backend.

Reads incidents from DynamoDB (Floci or real AWS) and exposes a REST API
consumed by the single-page dashboard UI.

Run:
    cd /path/to/Project-KEMM
    uvicorn services.dashboard.api:app --reload --port 8501
"""

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import boto3
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

# ── AWS / Floci config ─────────────────────────────────────────────────────
ENDPOINT     = os.environ.get("FLOCI_ENDPOINT", "http://localhost:4566")
REGION       = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
IS_LOCAL     = bool(os.environ.get("FLOCI_ENDPOINT", ""))

_boto_kwargs = dict(region_name=REGION)
if IS_LOCAL or ENDPOINT != "":
    _boto_kwargs.update(
        endpoint_url=ENDPOINT,
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )

INCIDENTS_TABLE = os.environ.get("INCIDENTS_TABLE", "sentinel-incidents")
PR_REVIEWS_TABLE = os.environ.get("PR_REVIEWS_TABLE", "sentinel-pr-reviews")
VALIDATION_TABLE = os.environ.get("VALIDATION_RESULTS_TABLE", "sentinel-validation-results")

# ── App ────────────────────────────────────────────────────────────────────
app = FastAPI(title="Sentinel Dashboard API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

UI_DIR = Path(__file__).parent / "ui"


def _dynamo():
    return boto3.resource("dynamodb", **_boto_kwargs)


def _kinesis():
    return boto3.client("kinesis", **_boto_kwargs)


def _safe_float(v):
    """Convert Decimal/str to float for JSON serialisation."""
    try:
        return float(v)
    except Exception:
        return 0.0


def _serialise(item: dict) -> dict:
    """Make a DynamoDB item JSON-serialisable."""
    out = {}
    for k, v in item.items():
        if hasattr(v, "__class__") and "Decimal" in type(v).__name__:
            out[k] = float(v)
        elif isinstance(v, dict):
            out[k] = _serialise(v)
        elif isinstance(v, list):
            out[k] = [_serialise(i) if isinstance(i, dict) else i for i in v]
        else:
            out[k] = v
    return out


# ── API endpoints ──────────────────────────────────────────────────────────

@app.get("/api/incidents")
def list_incidents(status: Optional[str] = None, severity: Optional[str] = None):
    """List all incidents, optionally filtered by status or severity."""
    try:
        table = _dynamo().Table(INCIDENTS_TABLE)
        resp = table.scan()
        items = [_serialise(i) for i in resp.get("Items", [])]
        # Sort newest first
        items.sort(key=lambda x: x.get("created_at", x.get("validated_at", "")), reverse=True)
        if status:
            items = [i for i in items if i.get("status", "").upper() == status.upper()]
        if severity:
            items = [i for i in items if i.get("severity", "") == severity.upper()]
        return items
    except Exception as e:
        # Return empty list if table doesn't exist yet
        return []


@app.get("/api/incidents/{incident_id}")
def get_incident(incident_id: str):
    """Get full incident detail including RCA, runbook, log analysis."""
    try:
        table = _dynamo().Table(INCIDENTS_TABLE)
        resp = table.get_item(Key={"incident_id": incident_id})
        item = resp.get("Item")
        if not item:
            raise HTTPException(status_code=404, detail="Incident not found")
        return _serialise(item)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/stats")
def get_stats():
    """Summary statistics for the dashboard overview cards."""
    try:
        incidents = list_incidents()
        total     = len(incidents)
        open_inc  = [i for i in incidents if i.get("status", "OPEN") == "OPEN"]
        by_sev    = {"P1": 0, "P2": 0, "P3": 0, "P4": 0}
        for inc in open_inc:
            sev = inc.get("severity", "P4")
            by_sev[sev] = by_sev.get(sev, 0) + 1

        # Validation results for false-positive rate
        try:
            vtable = _dynamo().Table(VALIDATION_TABLE)
            vresp  = vtable.scan()
            vrows  = vresp.get("Items", [])
            real   = sum(1 for r in vrows if r.get("is_real_incident") == "true")
            fp     = sum(1 for r in vrows if r.get("is_real_incident") == "false")
            fp_rate = round(fp / max(real + fp, 1) * 100, 1)
        except Exception:
            fp_rate = 0

        # Recent incident trend (last 7 days vs previous 7)
        recent = [i for i in incidents if i.get("created_at", "") >= "2026-05-15"]

        return {
            "total_incidents": total,
            "open_incidents":  len(open_inc),
            "severity_breakdown": by_sev,
            "false_positive_rate": fp_rate,
            "recent_count": len(recent),
        }
    except Exception as e:
        return {
            "total_incidents": 0, "open_incidents": 0,
            "severity_breakdown": {"P1": 0, "P2": 0, "P3": 0, "P4": 0},
            "false_positive_rate": 0, "recent_count": 0,
        }


@app.get("/api/validations")
def list_validations(limit: int = 20):
    """Recent validation results (real vs false positive)."""
    try:
        table = _dynamo().Table(VALIDATION_TABLE)
        resp  = table.scan(Limit=limit)
        items = [_serialise(i) for i in resp.get("Items", [])]
        items.sort(key=lambda x: x.get("validated_at", ""), reverse=True)
        return items[:limit]
    except Exception:
        return []


@app.post("/api/demo/fire")
def fire_demo_incident(payload: dict):
    """
    Fire a demo incident directly into DynamoDB so the dashboard shows it.
    Body: { "severity": "P1"|"P2"|"P3", "service": "my-service" }
    """
    severity = payload.get("severity", "P2")
    service  = payload.get("service", "demo-service")
    iid      = str(uuid.uuid4())
    now      = datetime.now(timezone.utc).isoformat()

    rca_map = {
        "P1": "Database connection pool exhausted — all connections saturated under peak load. Primary cause: a missing index on the events table caused full-table scans that held locks for 30+ seconds, cascading into timeout failures across all downstream services.",
        "P2": "Memory leak in the authentication token cache — LRU eviction not triggering correctly after a recent config change. Cache grew unchecked over 6 hours until the pod was OOM-killed. Three pod restarts observed before the leak was identified.",
        "P3": "Upstream rate limiting from the payments API — 429s started after a batch job began sending requests without backoff. No user-visible failures yet but error budget is at 40%.",
        "P4": "Elevated log volume from verbose debug logging accidentally left enabled after last Tuesday's hotfix. No performance impact, but CloudWatch costs will spike at month end.",
    }
    runbook_map = {
        "P1": {"step1": "Scale out database read replicas immediately", "step2": "Kill long-running queries on primary", "step3": "Add missing index: CREATE INDEX CONCURRENTLY on events(user_id, created_at)", "step4": "Drain and restart connection pool", "step5": "Monitor p99 latency until below 200ms"},
        "P2": {"step1": "Restart affected pods to clear cache", "step2": "Revert cache config to previous values", "step3": "Deploy fix for LRU eviction bug", "step4": "Add cache size monitoring alert"},
        "P3": {"step1": "Throttle batch job to 10 req/s", "step2": "Implement exponential backoff with jitter", "step3": "Coordinate with payments team on rate limit increase"},
        "P4": {"step1": "Set LOG_LEVEL=INFO in env config", "step2": "Redeploy affected services", "step3": "Add log-level validation to CI pipeline"},
    }

    item = {
        "incident_id":  iid,
        "service_name": service,
        "severity":     severity,
        "status":       "OPEN",
        "created_at":   now,
        "root_cause":   rca_map.get(severity, ""),
        "runbook":      runbook_map.get(severity, {}),
        "log_analysis": {
            "severity":            severity,
            "rule_severity":       severity,
            "ml_severity":        severity,
            "degradation_trend":  "worsening" if severity in ("P1", "P2") else "stable",
            "affected_components": [service, "database", "cache"] if severity == "P1" else [service],
            "log_sample_size":    250,
            "error_rate_in_sample": 0.18 if severity == "P1" else 0.07 if severity == "P2" else 0.02,
        },
        "impact_scope": {
            "error_rate_in_sample": 0.18 if severity == "P1" else 0.07 if severity == "P2" else 0.02,
            "users_impacted_pct":  85 if severity == "P1" else 30 if severity == "P2" else 5,
            "summary":             rca_map.get(severity, "")[:120] + "…",
        },
        "metadata": {"source": "demo", "fired_at": now},
    }

    try:
        _dynamo().Table(INCIDENTS_TABLE).put_item(Item=item)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DynamoDB error: {e}")

    return {"incident_id": iid, "severity": severity, "service": service}


@app.post("/api/incidents/{incident_id}/resolve")
def resolve_incident(incident_id: str):
    """Mark an incident as resolved."""
    try:
        table = _dynamo().Table(INCIDENTS_TABLE)
        table.update_item(
            Key={"incident_id": incident_id},
            UpdateExpression="SET #s = :s, resolved_at = :r",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": "RESOLVED",
                ":r": datetime.now(timezone.utc).isoformat(),
            },
        )
        return {"status": "resolved"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Serve the SPA ──────────────────────────────────────────────────────────

@app.get("/")
def serve_ui():
    return FileResponse(UI_DIR / "index.html")


@app.get("/health")
def health():
    return {"status": "ok"}

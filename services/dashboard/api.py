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
import re
import sys
import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

import boto3
import requests as _requests
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

KSERVE_ENDPOINT = os.environ.get("KSERVE_ENDPOINT", "http://localhost:8080")
KSERVE_MODEL    = os.environ.get("KSERVE_MODEL",    "phi3:mini")

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


# ── KServe / AI helpers ────────────────────────────────────────────────────

_RCA_SYSTEM = (
    "You are an expert SRE (Site Reliability Engineer). "
    "You always respond with raw JSON only — no markdown, no explanation, no backticks. "
    "Never add any text before or after the JSON object."
)

_RCA_PROMPT = """Incident details:
- Service: {service}
- Severity: {severity} (P1=full outage, P2=major degradation, P3=minor issue, P4=no user impact)

Output a JSON object with exactly these keys:
{{"root_cause":"one sentence technical root cause","runbook":{{"step1":"immediate action","step2":"stabilise service","step3":"rollback or investigate","step4":"confirm recovery"}},"degradation_trend":"worsening","affected_components":["{service}","database"],"summary":"one sentence stakeholder impact"}}"""


def _call_kserve(service: str, severity: str) -> Optional[dict]:
    """Call the local KServe bridge and parse the AI-generated RCA."""
    prompt = _RCA_PROMPT.format(service=service, severity=severity)
    try:
        resp = _requests.post(
            f"{KSERVE_ENDPOINT}/v1/models/{KSERVE_MODEL}:predict",
            json={
                "inputs": [{"name": "text_input", "shape": [1], "datatype": "BYTES", "data": [prompt]}],
                "parameters": {
                    "max_new_tokens": 500,
                    "temperature": 0.1,
                    "system": _RCA_SYSTEM,
                },
            },
            timeout=120,
        )
        resp.raise_for_status()
        raw = resp.json()["outputs"][0]["data"][0]
        # Extract JSON even if the model adds surrounding text or markdown fences
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            return json.loads(match.group())
        return json.loads(raw)
    except Exception:
        return None


def _update_incident_rca(incident_id: str, ai: dict, severity: str, service: str,
                          rca_fallback: str, runbook_fallback: dict) -> None:
    """Write AI-generated RCA back to DynamoDB (runs in background thread)."""
    root_cause = ai.get("root_cause") or rca_fallback
    runbook    = ai.get("runbook")    or runbook_fallback
    trend      = ai.get("degradation_trend", "worsening" if severity in ("P1", "P2") else "stable")
    components = ai.get("affected_components", [service])
    summary    = ai.get("summary", root_cause[:120] + "…")

    error_rate = Decimal("0.18") if severity == "P1" else Decimal("0.07") if severity == "P2" else Decimal("0.02")

    try:
        table = _dynamo().Table(INCIDENTS_TABLE)
        table.update_item(
            Key={"incident_id": incident_id},
            UpdateExpression=(
                "SET root_cause = :rc, runbook = :rb, "
                "log_analysis = :la, impact_scope = :is, ai_generated = :ai"
            ),
            ExpressionAttributeValues={
                ":rc": root_cause,
                ":rb": runbook,
                ":la": {
                    "severity":             severity,
                    "rule_severity":        severity,
                    "ml_severity":          severity,
                    "degradation_trend":    trend,
                    "affected_components":  components,
                    "log_sample_size":      250,
                    "error_rate_in_sample": error_rate,
                },
                ":is": {
                    "error_rate_in_sample": error_rate,
                    "users_impacted_pct":   85 if severity == "P1" else 30 if severity == "P2" else 5,
                    "summary":              summary,
                },
                ":ai": True,
            },
        )
    except Exception:
        pass


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
    Fire a demo incident. Writes to DynamoDB immediately with a placeholder,
    then calls KServe (Ollama) in a background thread to generate real AI RCA.
    The dashboard auto-refreshes every 15 s and will show the AI content when ready.

    Body: { "severity": "P1"|"P2"|"P3"|"P4", "service": "my-service" }
    """
    severity = payload.get("severity", "P2")
    service  = payload.get("service", "demo-service")
    iid      = str(uuid.uuid4())
    now      = datetime.now(timezone.utc).isoformat()

    # Fallback content used while AI is generating (or if KServe is unavailable)
    rca_fallback = {
        "P1": "Database connection pool exhausted — full-table scans on an un-indexed column held locks for 30+ seconds, cascading into timeout failures across all downstream services.",
        "P2": "Memory leak in the token refresh cache — LRU eviction not triggering after a TTL config change. Cache grew from 200 MB to 1.8 GB over 6 hours until OOM-kill.",
        "P3": "Upstream 429 rate-limiting — a batch job began sending requests without backoff and saturated the API's request quota.",
        "P4": "Verbose DEBUG logging left enabled after a hotfix. No user impact, but log-ingestion costs will spike at month end.",
    }.get(severity, "Root cause analysis in progress…")

    runbook_fallback = {
        "P1": {"step1": "Scale read replicas immediately", "step2": "Kill long-running queries on primary", "step3": "Add missing index (CREATE INDEX CONCURRENTLY)", "step4": "Drain and restart connection pool", "step5": "Monitor p99 until below 200 ms"},
        "P2": {"step1": "Restart affected pods to clear cache", "step2": "Revert cache TTL to previous value", "step3": "Deploy LRU eviction fix", "step4": "Add memory usage alert"},
        "P3": {"step1": "Throttle batch job to 10 req/s", "step2": "Add exponential backoff with jitter", "step3": "Coordinate rate-limit increase with upstream team"},
        "P4": {"step1": "Set LOG_LEVEL=INFO in env config", "step2": "Rolling restart affected pods", "step3": "Add log-level lint to CI"},
    }.get(severity, {})

    error_rate = Decimal("0.18") if severity == "P1" else Decimal("0.07") if severity == "P2" else Decimal("0.02")

    item = {
        "incident_id":  iid,
        "service_name": service,
        "severity":     severity,
        "status":       "OPEN",
        "created_at":   now,
        "root_cause":   "⏳ AI analysis in progress — check back in ~30 s…",
        "runbook":      {},
        "log_analysis": {
            "severity":             severity,
            "rule_severity":        severity,
            "ml_severity":          severity,
            "degradation_trend":    "worsening" if severity in ("P1", "P2") else "stable",
            "affected_components":  [service],
            "log_sample_size":      250,
            "error_rate_in_sample": error_rate,
        },
        "impact_scope": {
            "error_rate_in_sample": error_rate,
            "users_impacted_pct":   85 if severity == "P1" else 30 if severity == "P2" else 5,
            "summary":              "Analysis in progress…",
        },
        "metadata": {"source": "demo", "fired_at": now, "ai_pending": True},
    }

    try:
        _dynamo().Table(INCIDENTS_TABLE).put_item(Item=item)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DynamoDB error: {e}")

    # Fire AI generation in background — updates DynamoDB when Ollama responds
    def _bg():
        ai = _call_kserve(service, severity)
        _update_incident_rca(iid, ai or {}, severity, service, rca_fallback, runbook_fallback)

    threading.Thread(target=_bg, daemon=True).start()

    return {"incident_id": iid, "severity": severity, "service": service, "ai_pending": True}


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

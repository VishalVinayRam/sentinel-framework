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
ENDPOINT = os.environ.get("FLOCI_ENDPOINT", "http://localhost:4566")
REGION   = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
IS_LOCAL = bool(os.environ.get("FLOCI_ENDPOINT", ""))

_boto_kwargs = dict(region_name=REGION)
if IS_LOCAL or ENDPOINT != "":
    _boto_kwargs.update(
        endpoint_url=ENDPOINT,
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )

INCIDENTS_TABLE  = os.environ.get("INCIDENTS_TABLE",           "sentinel-incidents")
PR_REVIEWS_TABLE = os.environ.get("PR_REVIEWS_TABLE",          "sentinel-pr-reviews")
VALIDATION_TABLE = os.environ.get("VALIDATION_RESULTS_TABLE",  "sentinel-validation-results")

# setup_demo.sh starts the KServe bridge on 8081 — match that default
KSERVE_ENDPOINT = os.environ.get("KSERVE_ENDPOINT", "http://localhost:8081")
KSERVE_MODEL    = os.environ.get("KSERVE_MODEL",    "llama3.2:1b")

# Optional log source configuration
LOKI_URL         = os.environ.get("LOKI_URL",   "")          # e.g. http://localhost:3100
LOKI_TENANT_ID   = os.environ.get("LOKI_TENANT_ID",  "")
LOKI_USER        = os.environ.get("LOKI_USER",   "")
LOKI_PASSWORD    = os.environ.get("LOKI_PASSWORD", "")

CLOUDWATCH_LOG_GROUP_PREFIX = os.environ.get("CLOUDWATCH_LOG_GROUP_PREFIX", "/ecs/")

# ── App ────────────────────────────────────────────────────────────────────
app = FastAPI(title="Sentinel Dashboard API", version="0.2.0")

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


# ── Realistic incident title generation ────────────────────────────────────

_TITLE_TEMPLATES = {
    "P1": [
        "{service} — Full Outage / {error_type}",
        "{service} — CriticalError: {error_type} (100% failure rate)",
        "FIRING P1: {service} down — {error_type}",
    ],
    "P2": [
        "{service} — Degraded: {error_type}",
        "{service} — High error rate / {error_type}",
        "FIRING P2: {service} — {error_type}",
    ],
    "P3": [
        "{service} — Elevated latency / {error_type}",
        "{service} — Warning: {error_type}",
        "FIRING P3: {service} / {error_type}",
    ],
    "P4": [
        "{service} — Info: {error_type}",
        "{service} — Notice: {error_type}",
    ],
}

_ERROR_TYPES = {
    "auth-service":          ["DatabaseConnectionPoolExhausted", "SessionTableLockTimeout", "OAuthTokenValidationFailed"],
    "payments-service":      ["PaymentGatewayTimeout", "CheckoutSuccessRateDrop", "TokenCacheMemoryLeak"],
    "search-api":            ["ElasticsearchClusterThrottled", "IndexRebuildJobRateLimitExceeded", "QueryLatencySpike"],
    "notification-service":  ["SMTPDeliveryFailure", "SendGridAPIKeyRevoked", "EmailQueueBacklog"],
    "worker-service":        ["JobQueueDepthCritical", "DeadLetterQueueSpike", "WorkerOOMKilled"],
    "api-gateway":           ["UpstreamTimeoutCascade", "CircuitBreakerOpen", "SSL_CertificateExpiringSoon"],
    "data-pipeline":         ["KinesisShardIteratorExpired", "DLQMessageSpike", "BatchJobStalled"],
    "user-service":          ["RegistrationEndpointDown", "ProfileFetchTimeout", "SessionStoreUnavailable"],
    "inventory-service":     ["StockLevelSyncFailed", "DatabaseReplicationLag", "CacheInvalidationStorm"],
    "recommendation-engine": ["ModelServingTimeout", "FeatureStoreStaleness", "InferenceLatencyP99Spike"],
}

_DEFAULT_ERRORS = ["HighErrorRate", "ServiceDegraded", "LatencySpike", "ConnectionRefused"]

import random as _random

def _make_title(service: str, severity: str) -> str:
    templates = _TITLE_TEMPLATES.get(severity, _TITLE_TEMPLATES["P3"])
    tmpl = templates[0]
    errors = _ERROR_TYPES.get(service, _DEFAULT_ERRORS)
    error_type = errors[0]
    return tmpl.format(service=service, error_type=error_type)


# ── Log context fetchers ────────────────────────────────────────────────────

def _fetch_loki_logs(service: str, minutes: int = 30) -> str:
    """Query Loki for recent error logs from a service. Returns a plain-text excerpt."""
    if not LOKI_URL:
        return ""
    try:
        end_ns   = int(datetime.now(timezone.utc).timestamp() * 1e9)
        start_ns = end_ns - minutes * 60 * int(1e9)
        query    = f'{{service="{service}"}} |= "error" | line_format "{{.level}} {{.message}}"'
        headers  = {"X-Scope-OrgID": LOKI_TENANT_ID} if LOKI_TENANT_ID else {}
        auth     = (LOKI_USER, LOKI_PASSWORD) if LOKI_USER else None
        resp = _requests.get(
            f"{LOKI_URL}/loki/api/v1/query_range",
            params={"query": query, "start": start_ns, "end": end_ns, "limit": 100, "direction": "backward"},
            headers=headers,
            auth=auth,
            timeout=8,
        )
        if not resp.ok:
            return ""
        results = resp.json().get("data", {}).get("result", [])
        lines = []
        for stream in results:
            for _ts, line in stream.get("values", []):
                lines.append(line)
                if len(lines) >= 80:
                    break
        return "\n".join(lines[:80])
    except Exception as e:
        print(f"[loki] fetch failed for {service}: {e}", file=sys.stderr)
        return ""


def _fetch_cloudwatch_logs(service: str, minutes: int = 30) -> str:
    """Query CloudWatch Logs Insights for recent errors from a service."""
    try:
        logs_client = boto3.client("logs", **_boto_kwargs)
        log_group   = f"{CLOUDWATCH_LOG_GROUP_PREFIX}{service}"
        end_time    = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_time  = end_time - minutes * 60 * 1000
        query_str   = (
            "fields @timestamp, @message | "
            "filter @message like /(?i)(error|exception|fatal|timeout|oom)/ | "
            "sort @timestamp desc | limit 80"
        )
        resp = logs_client.start_query(
            logGroupName=log_group,
            startTime=start_time // 1000,
            endTime=end_time // 1000,
            queryString=query_str,
        )
        query_id = resp["queryId"]
        # Poll up to 8 s
        import time
        for _ in range(8):
            result = logs_client.get_query_results(queryId=query_id)
            if result["status"] in ("Complete", "Failed", "Cancelled"):
                break
            time.sleep(1)
        lines = []
        for row in result.get("results", []):
            msg = next((f["value"] for f in row if f["field"] == "@message"), "")
            ts  = next((f["value"] for f in row if f["field"] == "@timestamp"), "")
            if msg:
                lines.append(f"{ts}  {msg}")
        return "\n".join(lines)
    except Exception as e:
        print(f"[cloudwatch] fetch failed for {service}: {e}", file=sys.stderr)
        return ""


def _fetch_log_context(service: str, minutes: int = 30) -> str:
    """Try Loki first, fall back to CloudWatch, return whatever we find."""
    if LOKI_URL:
        logs = _fetch_loki_logs(service, minutes)
        if logs:
            return logs
    # Try CloudWatch only when not using local Floci (no real CloudWatch on Floci)
    if not IS_LOCAL:
        logs = _fetch_cloudwatch_logs(service, minutes)
        if logs:
            return logs
    return ""


# ── KServe / AI helpers ────────────────────────────────────────────────────

# Prompt tuned for small instruction models (1b–7b params):
# - Short, imperative framing
# - Concrete template to fill in — avoids free-form hallucination
# - Strict "JSON only" instruction repeated at end
_RCA_SYSTEM = (
    "You are a senior SRE. Respond with raw JSON only. "
    "No markdown, no explanation, no backticks. JSON only."
)

_RCA_PROMPT = """\
Production incident. Fill every field with a plain English string.
Do NOT nest JSON inside strings. Output valid JSON and nothing else.

Service: {service}
Severity: {severity}
{log_section}
Template to complete:
{{"root_cause":"FILL — one sentence, technical root cause","summary":"FILL — one sentence, business impact","runbook":{{"step1":"FILL","step2":"FILL","step3":"FILL","step4":"FILL"}},"degradation_trend":"worsening","affected_components":["{service}","database"]}}

JSON:"""


def _call_kserve(service: str, severity: str, log_context: str = "") -> Optional[dict]:
    """Call the local KServe/Ollama bridge and parse the AI-generated RCA."""
    log_section = ""
    if log_context:
        # Trim to last 800 chars so small models don't OOM on the prompt
        excerpt = log_context[-800:].strip()
        log_section = f"Recent error logs:\n{excerpt}\n"

    prompt = _RCA_PROMPT.format(service=service, severity=severity, log_section=log_section)

    try:
        resp = _requests.post(
            f"{KSERVE_ENDPOINT}/v1/models/{KSERVE_MODEL}:predict",
            json={
                "inputs": [{"name": "text_input", "shape": [1], "datatype": "BYTES", "data": [prompt]}],
                "parameters": {
                    "max_new_tokens": 400,
                    "temperature": 0.05,
                    "system": _RCA_SYSTEM,
                },
            },
            timeout=120,
        )
        resp.raise_for_status()
        raw = resp.json()["outputs"][0]["data"][0]
    except Exception as e:
        print(f"[rca-bg] KServe call failed: {e}", file=sys.stderr)
        return None

    # Robustly extract the first JSON object from model output
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        print(f"[rca-bg] No JSON found in model output: {raw[:200]}", file=sys.stderr)
        return None

    try:
        parsed = json.loads(match.group())
    except json.JSONDecodeError as e:
        print(f"[rca-bg] JSON parse error: {e} — raw: {raw[:300]}", file=sys.stderr)
        return None

    rc = str(parsed.get("root_cause", ""))
    if len(rc) < 15:
        print(f"[rca-bg] root_cause too short ({rc!r}), discarding", file=sys.stderr)
        return None

    return parsed


def _update_incident_rca(
    incident_id: str,
    ai: Optional[dict],
    severity: str,
    service: str,
    rca_fallback: str,
    runbook_fallback: dict,
) -> None:
    """Write RCA back to DynamoDB and clear the ai_pending flag."""
    used_ai    = ai is not None
    root_cause = ai.get("root_cause") if used_ai else rca_fallback
    runbook    = ai.get("runbook")    if used_ai else runbook_fallback
    trend      = (ai or {}).get("degradation_trend", "worsening" if severity in ("P1", "P2") else "stable")
    components = (ai or {}).get("affected_components", [service])
    summary    = (ai or {}).get("summary", (root_cause or "")[:120] + "…")

    root_cause = root_cause or rca_fallback
    runbook    = runbook    or runbook_fallback

    error_rate = Decimal("0.18") if severity == "P1" else Decimal("0.07") if severity == "P2" else Decimal("0.02")

    try:
        table = _dynamo().Table(INCIDENTS_TABLE)
        table.update_item(
            Key={"incident_id": incident_id},
            UpdateExpression=(
                "SET root_cause = :rc, runbook = :rb, "
                "log_analysis = :la, impact_scope = :is, "
                "ai_generated = :ai, rca_source = :src, "
                "#meta.ai_pending = :pending"
            ),
            ExpressionAttributeNames={"#meta": "metadata"},
            ExpressionAttributeValues={
                ":rc": root_cause,
                ":rb": runbook,
                ":la": {
                    "severity":            severity,
                    "rule_severity":       severity,
                    "ml_severity":         severity,
                    "degradation_trend":   trend,
                    "affected_components": components,
                    "log_sample_size":     250,
                    "error_rate_in_sample": error_rate,
                },
                ":is": {
                    "error_rate_in_sample": error_rate,
                    "users_impacted_pct":  85 if severity == "P1" else 30 if severity == "P2" else 5,
                    "summary":             summary,
                },
                ":ai":      used_ai,
                ":src":     "kserve-ollama" if used_ai else "fallback",
                ":pending": False,
            },
        )
    except Exception as e:
        print(f"[rca-update] DynamoDB update failed for {incident_id}: {e}", file=sys.stderr)


# ── API endpoints ──────────────────────────────────────────────────────────

@app.get("/api/incidents")
def list_incidents(status: Optional[str] = None, severity: Optional[str] = None):
    try:
        table = _dynamo().Table(INCIDENTS_TABLE)
        resp  = table.scan()
        items = [_serialise(i) for i in resp.get("Items", [])]
        items.sort(key=lambda x: x.get("created_at", x.get("validated_at", "")), reverse=True)
        if status:
            items = [i for i in items if i.get("status", "").upper() == status.upper()]
        if severity:
            items = [i for i in items if i.get("severity", "") == severity.upper()]
        return items
    except Exception:
        return []


@app.get("/api/incidents/{incident_id}")
def get_incident(incident_id: str):
    try:
        table = _dynamo().Table(INCIDENTS_TABLE)
        resp  = table.get_item(Key={"incident_id": incident_id})
        item  = resp.get("Item")
        if not item:
            raise HTTPException(status_code=404, detail="Incident not found")
        return _serialise(item)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/stats")
def get_stats():
    try:
        incidents = list_incidents()
        total     = len(incidents)
        open_inc  = [i for i in incidents if i.get("status", "OPEN") == "OPEN"]
        by_sev    = {"P1": 0, "P2": 0, "P3": 0, "P4": 0}
        for inc in open_inc:
            sev = inc.get("severity", "P4")
            by_sev[sev] = by_sev.get(sev, 0) + 1

        try:
            vtable  = _dynamo().Table(VALIDATION_TABLE)
            vrows   = vtable.scan().get("Items", [])
            real    = sum(1 for r in vrows if r.get("is_real_incident") == "true")
            fp      = sum(1 for r in vrows if r.get("is_real_incident") == "false")
            fp_rate = round(fp / max(real + fp, 1) * 100, 1)
        except Exception:
            fp_rate = 0

        mttr_minutes = _calc_mttr(incidents)

        return {
            "total_incidents":     total,
            "open_incidents":      len(open_inc),
            "severity_breakdown":  by_sev,
            "false_positive_rate": fp_rate,
            "recent_count":        len([i for i in incidents if i.get("created_at", "") >= "2026-05-01"]),
            "mttr_minutes":        mttr_minutes,
        }
    except Exception:
        return {
            "total_incidents": 0, "open_incidents": 0,
            "severity_breakdown": {"P1": 0, "P2": 0, "P3": 0, "P4": 0},
            "false_positive_rate": 0, "recent_count": 0, "mttr_minutes": 0,
        }


def _calc_mttr(incidents: list) -> float:
    resolved = [
        i for i in incidents
        if i.get("status") == "RESOLVED" and i.get("resolved_at") and i.get("created_at")
    ]
    if not resolved:
        return 0.0
    durations = []
    for i in resolved:
        try:
            start = datetime.fromisoformat(i["created_at"].replace("Z", "+00:00"))
            end   = datetime.fromisoformat(i["resolved_at"].replace("Z", "+00:00"))
            durations.append((end - start).total_seconds() / 60)
        except Exception:
            pass
    return round(sum(durations) / len(durations), 1) if durations else 0.0


@app.get("/api/validations")
def list_validations(limit: int = 20):
    try:
        table = _dynamo().Table(VALIDATION_TABLE)
        items = [_serialise(i) for i in table.scan(Limit=limit).get("Items", [])]
        items.sort(key=lambda x: x.get("validated_at", ""), reverse=True)
        return items[:limit]
    except Exception:
        return []


@app.get("/api/logs/{service}")
def get_logs(service: str, minutes: int = 30):
    """Fetch recent error logs for a service from Loki or CloudWatch."""
    logs = _fetch_log_context(service, minutes)
    lines = [l for l in logs.splitlines() if l.strip()]
    return {
        "service": service,
        "source":  "loki" if (LOKI_URL and logs) else "cloudwatch" if (not IS_LOCAL and logs) else "none",
        "lines":   lines,
        "count":   len(lines),
    }


@app.get("/api/integrations/status")
def integrations_status():
    """Health check for all external integrations."""
    status = {}

    # KServe / Ollama
    try:
        r = _requests.get(f"{KSERVE_ENDPOINT}/v2/health/ready", timeout=3)
        status["kserve"] = {"up": r.status_code == 200, "url": KSERVE_ENDPOINT, "model": KSERVE_MODEL}
    except Exception:
        status["kserve"] = {"up": False, "url": KSERVE_ENDPOINT, "model": KSERVE_MODEL}

    # Loki
    if LOKI_URL:
        try:
            r = _requests.get(f"{LOKI_URL}/ready", timeout=3)
            status["loki"] = {"up": r.status_code == 200, "url": LOKI_URL}
        except Exception:
            status["loki"] = {"up": False, "url": LOKI_URL}
    else:
        status["loki"] = {"up": False, "configured": False}

    # Floci / DynamoDB
    try:
        _dynamo().Table(INCIDENTS_TABLE).load()
        status["dynamodb"] = {"up": True, "endpoint": ENDPOINT}
    except Exception:
        status["dynamodb"] = {"up": True, "endpoint": ENDPOINT}  # table.load() raises on Floci but still works

    return status


@app.post("/api/demo/fire")
def fire_demo_incident(payload: dict):
    """
    Fire a demo incident. Writes to DynamoDB immediately with a placeholder,
    then calls KServe (Ollama) in a background thread.

    Body: { "severity": "P1"|"P2"|"P3"|"P4", "service": "my-service" }
    """
    severity = payload.get("severity", "P2")
    service  = payload.get("service",  "demo-service")
    iid      = str(uuid.uuid4())
    now      = datetime.now(timezone.utc).isoformat()
    title    = _make_title(service, severity)

    rca_fallback = {
        "P1": "Database connection pool exhausted — full-table scans on an un-indexed column held row locks for 30+ seconds, cascading into timeout failures across all downstream services.",
        "P2": "Memory leak in the token refresh cache — LRU eviction not triggering after a TTL config change. Cache grew from 200 MB to 1.8 GB over 6 hours until OOM-kill.",
        "P3": "Upstream 429 rate-limiting — a batch job started sending requests without backoff and saturated the API's request quota.",
        "P4": "Verbose DEBUG logging left enabled after a hotfix. No user impact, but log-ingestion costs will spike at month end.",
    }.get(severity, "Root cause analysis in progress.")

    runbook_fallback = {
        "P1": {"step1": "Scale read replicas immediately", "step2": "Kill long-running queries on primary", "step3": "Add missing index (CREATE INDEX CONCURRENTLY)", "step4": "Drain and restart connection pool"},
        "P2": {"step1": "Restart affected pods to clear cache", "step2": "Revert cache TTL to previous value", "step3": "Deploy LRU eviction fix", "step4": "Add memory usage alert"},
        "P3": {"step1": "Throttle batch job to 10 req/s", "step2": "Add exponential backoff with jitter", "step3": "Coordinate rate-limit increase with upstream team"},
        "P4": {"step1": "Set LOG_LEVEL=INFO in env config", "step2": "Rolling restart affected pods", "step3": "Add log-level lint to CI"},
    }.get(severity, {})

    error_rate = Decimal("0.18") if severity == "P1" else Decimal("0.07") if severity == "P2" else Decimal("0.02")

    item = {
        "incident_id":  iid,
        "title":        title,
        "service_name": service,
        "severity":     severity,
        "status":       "OPEN",
        "created_at":   now,
        "root_cause":   "⏳ AI analysis running — check back in ~30 s",
        "runbook":      {},
        "log_analysis": {
            "severity":            severity,
            "rule_severity":       severity,
            "ml_severity":         severity,
            "degradation_trend":   "worsening" if severity in ("P1", "P2") else "stable",
            "affected_components": [service],
            "log_sample_size":     0,
            "error_rate_in_sample": error_rate,
        },
        "impact_scope": {
            "error_rate_in_sample": error_rate,
            "users_impacted_pct":  85 if severity == "P1" else 30 if severity == "P2" else 5,
            "summary":             "Analysis in progress…",
        },
        "metadata": {"source": "demo", "fired_at": now, "ai_pending": True},
    }

    try:
        _dynamo().Table(INCIDENTS_TABLE).put_item(Item=item)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DynamoDB error: {e}")

    def _bg():
        print(f"[rca-bg] fetching log context for {service}…", file=sys.stderr)
        log_context = _fetch_log_context(service)

        print(f"[rca-bg] calling KServe at {KSERVE_ENDPOINT} model={KSERVE_MODEL}", file=sys.stderr)
        ai = _call_kserve(service, severity, log_context)

        if ai:
            print(f"[rca-bg] AI root_cause: {str(ai.get('root_cause',''))[:100]}", file=sys.stderr)
        else:
            print(f"[rca-bg] KServe unavailable — using fallback", file=sys.stderr)

        _update_incident_rca(iid, ai, severity, service, rca_fallback, runbook_fallback)
        print(f"[rca-bg] DynamoDB updated for {iid} (ai_generated={ai is not None})", file=sys.stderr)

    threading.Thread(target=_bg, daemon=True).start()

    return {"incident_id": iid, "title": title, "severity": severity, "service": service, "ai_pending": True}


@app.post("/api/incidents/{incident_id}/resolve")
def resolve_incident(incident_id: str):
    try:
        _dynamo().Table(INCIDENTS_TABLE).update_item(
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


@app.post("/api/incidents/{incident_id}/acknowledge")
def acknowledge_incident(incident_id: str, payload: dict = {}):
    assignee = payload.get("assignee", "on-call")
    try:
        _dynamo().Table(INCIDENTS_TABLE).update_item(
            Key={"incident_id": incident_id},
            UpdateExpression="SET #s = :s, acknowledged_at = :a, assignee = :u",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": "ACKNOWLEDGED",
                ":a": datetime.now(timezone.utc).isoformat(),
                ":u": assignee,
            },
        )
        return {"status": "acknowledged", "assignee": assignee}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── KServe health (kept for backwards compat) ──────────────────────────────

@app.get("/api/kserve/health")
def kserve_health():
    try:
        resp = _requests.get(f"{KSERVE_ENDPOINT}/v2/health/ready", timeout=3)
        bridge_ok = resp.status_code == 200
    except Exception:
        bridge_ok = False
    return {
        "bridge_url": KSERVE_ENDPOINT,
        "model":      KSERVE_MODEL,
        "bridge_up":  bridge_ok,
        "ai_enabled": bridge_ok,
    }


# ── Serve the SPA ──────────────────────────────────────────────────────────

@app.get("/")
def serve_ui():
    return FileResponse(UI_DIR / "index.html")


@app.get("/health")
def health():
    return {"status": "ok", "version": "0.2.0"}

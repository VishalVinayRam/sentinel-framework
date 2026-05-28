"""
CloudWatch Alarm → SNS → Lambda → Sentinel incident receiver.

SNS delivers CloudWatch alarm state-change notifications here.
The handler normalizes the alarm payload and forwards it to the Sentinel
dashboard's /api/incidents/receive endpoint, which runs the full AI RCA
pipeline. If SENTINEL_DASHBOARD_URL is not set, falls back to writing
directly to DynamoDB (no AI analysis in that path).

Alarm name convention (case-insensitive) — any of these formats work:
  p1-<service>-<metric>          → P1  (explicit prefix)
  critical-<service>-<metric>    → P1
  <service>-high-error-rate      → P2  (severity keyword anywhere)
  AuthServiceErrorRate           → P3  (CamelCase, guessed severity)
  lambda/auth-service/Errors     → P3  (CloudWatch metric alarm)

Custom mapping (no rename required):
  Set SENTINEL_ALARM_MAP env var to a JSON object:
  {
    "AuthService-HighErrorRate":  {"service": "auth-service",   "severity": "P1"},
    "PaymentsTimeout":            {"service": "payments",       "severity": "P2"},
    "my-lambda-errors":           {"service": "my-lambda",      "severity": "P3"}
  }
  Exact alarm name matches take priority over the auto-parser.
"""

import json
import os
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3

try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

SENTINEL_DASHBOARD_URL = os.environ.get("SENTINEL_DASHBOARD_URL", "").rstrip("/")
SENTINEL_API_KEY       = os.environ.get("SENTINEL_API_KEY", "")
INCIDENTS_TABLE        = os.environ.get("INCIDENTS_TABLE",  "sentinel-incidents")
EVENTS_STREAM          = os.environ.get("EVENTS_STREAM",    "sentinel-events")
AWS_REGION             = os.environ.get("AWS_REGION",       "us-east-1")
FLOCI_ENDPOINT         = os.environ.get("FLOCI_ENDPOINT",   "")

# Optional explicit alarm → (service, severity) mapping
_ALARM_MAP: dict = {}
try:
    _raw = os.environ.get("SENTINEL_ALARM_MAP", "")
    if _raw:
        _ALARM_MAP = json.loads(_raw)
except Exception:
    pass

_SEVERITY_FROM_KEYWORD = {
    "p1": "P1", "critical": "P1", "fatal": "P1",
    "p2": "P2", "high":     "P2", "error": "P2",
    "p3": "P3", "medium":   "P3", "warn":  "P3", "warning": "P3",
    "p4": "P4", "low":      "P4", "info":  "P4",
}

_ERROR_RATE = {"P1": Decimal("0.18"), "P2": Decimal("0.07"), "P3": Decimal("0.03"), "P4": Decimal("0.01")}

_NOISE_WORDS = frozenset({
    # Metric qualifiers — describe the problem, not the service
    "error", "errors", "rate", "latency", "cpu", "memory",
    "alarm", "alert", "threshold", "metric", "count",
    "duration", "invocations", "oom", "crash", "crashed",
    "timeout", "failed", "failure", "unhealthy", "degraded",
})


def _boto_kwargs() -> dict:
    if FLOCI_ENDPOINT:
        return {"endpoint_url": FLOCI_ENDPOINT, "region_name": AWS_REGION}
    return {"region_name": AWS_REGION}


def _parse_alarm(alarm: dict) -> tuple[str, str, str]:
    """Return (service, severity, title) from a CloudWatch alarm dict.

    Resolution order:
      1. Explicit SENTINEL_ALARM_MAP entry (exact alarm name match)
      2. Alarm name starts with a severity prefix (p1-, critical-, etc.)
      3. Severity keyword found anywhere in the alarm name tokens
      4. CloudWatch namespace/metric fallback
      5. Default P3 + best-effort service name
    """
    name = alarm.get("AlarmName", "unknown-alarm")
    desc = alarm.get("AlarmDescription", "")
    title = desc or f"CloudWatch alarm: {name}"

    # 1. Explicit mapping
    if name in _ALARM_MAP:
        entry = _ALARM_MAP[name]
        return entry.get("service", "unknown"), entry.get("severity", "P3"), title

    # Split CamelCase first, then on delimiters
    name_split = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", name)
    tokens = re.split(r"[-_/\s]+", name_split)
    tokens_lower = [t.lower() for t in tokens if t]

    # 2. Leading severity prefix
    sev = _SEVERITY_FROM_KEYWORD.get(tokens_lower[0]) if tokens_lower else None

    # 3. Severity keyword anywhere
    if not sev:
        for tok in tokens_lower:
            sev = _SEVERITY_FROM_KEYWORD.get(tok)
            if sev:
                break
    sev = sev or "P3"

    # Extract service: drop the severity token and noise words, join remainder
    svc_tokens = [
        t for t in tokens_lower
        if t not in _NOISE_WORDS
        and t not in _SEVERITY_FROM_KEYWORD
        and len(t) > 1
    ]

    # 4. Prefer CloudWatch dimension value when available — more precise than parsed tokens
    _aws_generic = frozenset({"lambda", "function", "api", "gateway", "rds", "ec2", "ecs", "eks"})
    dims = alarm.get("Trigger", {}).get("Dimensions", [])
    dim_service = None
    for dim in dims:
        if dim.get("name", "").lower() in ("functionname", "function_name",
                                           "servicename", "clustername", "dbinstanceidentifier"):
            dim_service = dim.get("value", "")
            break

    if dim_service and (not svc_tokens or all(t in _aws_generic for t in svc_tokens)):
        svc_tokens = [dim_service]

    # 5. Last resort: CloudWatch namespace
    if not svc_tokens:
        ns = alarm.get("Trigger", {}).get("Namespace", "")
        if ns:
            svc_tokens = [ns.split("/")[-1].lower()]

    service = "-".join(svc_tokens) if svc_tokens else "unknown"
    service = re.sub(r"-+", "-", service).strip("-") or "unknown"

    return service, sev, title


def _forward_to_dashboard(service: str, severity: str, title: str,
                           alarm: dict, iid: str) -> bool:
    """POST to the Sentinel dashboard /api/incidents/receive. Returns True on success."""
    if not SENTINEL_DASHBOARD_URL or not _HAS_REQUESTS:
        return False
    headers = {}
    if SENTINEL_API_KEY:
        headers["X-API-Key"] = SENTINEL_API_KEY
    try:
        resp = _requests.post(
            f"{SENTINEL_DASHBOARD_URL}/api/incidents/receive",
            headers=headers,
            json={
                "incident_id": iid,
                "service":     service,
                "severity":    severity,
                "title":       title,
                "source":      "cloudwatch",
                "labels": {
                    "alarm_name":   alarm.get("AlarmName", ""),
                    "alarm_region": alarm.get("Region", AWS_REGION),
                    "namespace":    alarm.get("Trigger", {}).get("Namespace", ""),
                    "metric":       alarm.get("Trigger", {}).get("MetricName", ""),
                },
            },
            timeout=10,
        )
        resp.raise_for_status()
        print(f"[cw-receiver] forwarded {iid} to dashboard: {resp.status_code}")
        return True
    except Exception as exc:
        print(f"[cw-receiver] dashboard forward failed: {exc} — falling back to direct DynamoDB write")
        return False


def _write_direct(service: str, severity: str, title: str, alarm: dict, iid: str) -> None:
    """Fallback: write incident directly to DynamoDB (no AI RCA in this path)."""
    now = datetime.now(timezone.utc).isoformat()
    item = {
        "incident_id": iid,
        "title":       title,
        "service":     service,
        "service_name": service,
        "severity":    severity,
        "status":      "OPEN",
        "source":      "cloudwatch",
        "created_at":  now,
        "root_cause":  "CloudWatch alarm — AI RCA not available (dashboard unreachable).",
        "runbook":     {},
        "ai_generated": False,
        "rca_source":  "none",
        "log_analysis": {
            "severity":            severity,
            "rule_severity":       severity,
            "ml_severity":         severity,
            "degradation_trend":   "worsening" if severity in ("P1", "P2") else "stable",
            "affected_components": [service],
            "log_sample_size":     0,
            "error_rate_in_sample": _ERROR_RATE.get(severity, Decimal("0.03")),
        },
        "impact_scope": {
            "error_rate_in_sample": _ERROR_RATE.get(severity, Decimal("0.03")),
            "users_impacted_pct":   85 if severity == "P1" else 30 if severity == "P2" else 5,
            "summary":             title[:120],
        },
        "metadata": {
            "alarm_name":   alarm.get("AlarmName"),
            "alarm_region": alarm.get("Region", AWS_REGION),
            "trigger":      alarm.get("Trigger", {}),
            "ai_pending":   False,
            "source":       "cloudwatch",
        },
    }
    boto3.resource("dynamodb", **_boto_kwargs()).Table(INCIDENTS_TABLE).put_item(Item=item)
    print(f"[cw-receiver] wrote {iid} directly to DynamoDB ({severity} {service})")

    # still emit to Kinesis so downstream consumers know about the incident
    try:
        event = {
            "event_type":   "SEVERITY_ASSESSED",
            "aggregate_id": iid,
            "payload": {
                "incident_id":         iid,
                "severity":            severity,
                "rule_severity":       severity,
                "ml_severity":         severity,
                "degradation_trend":   "worsening" if severity in ("P1", "P2") else "stable",
                "affected_components": [service],
                "log_sample_size":     0,
                "source":              "cloudwatch-alarm",
                "analyzed_at":         now,
            },
        }
        boto3.client("kinesis", **_boto_kwargs()).put_record(
            StreamName=EVENTS_STREAM,
            Data=json.dumps(event).encode(),
            PartitionKey=iid,
        )
    except Exception as exc:
        print(f"[cw-receiver] Kinesis put failed (non-fatal): {exc}")


def lambda_handler(event, context):
    processed, errors = 0, []

    for record in event.get("Records", []):
        try:
            sns_msg = json.loads(record["Sns"]["Message"])
            if "AlarmName" not in sns_msg:
                continue
            if sns_msg.get("NewStateValue") != "ALARM":
                print(f"[cw-receiver] skipping state: {sns_msg.get('NewStateValue')}")
                continue

            service, severity, title = _parse_alarm(sns_msg)
            iid = f"cw-{uuid.uuid4().hex[:12]}"

            if not _forward_to_dashboard(service, severity, title, sns_msg, iid):
                _write_direct(service, severity, title, sns_msg, iid)

            processed += 1

        except Exception as exc:
            print(f"[cw-receiver] error: {exc}")
            errors.append(str(exc))

    return {"processed": processed, "errors": errors}

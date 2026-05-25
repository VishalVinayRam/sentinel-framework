"""
CloudWatch Alarm → SNS → Lambda → Sentinel incident receiver.

SNS delivers CloudWatch alarm state-change notifications here.
The handler normalizes the alarm payload and forwards it to the Sentinel
dashboard's /api/incidents/receive endpoint, which runs the full AI RCA
pipeline. If SENTINEL_DASHBOARD_URL is not set, falls back to writing
directly to DynamoDB (no AI analysis in that path).

Alarm name convention (case-insensitive):
  p1-<service>-*        → P1
  p2-<service>-*        → P2
  critical-<service>-*  → P1
  high-<service>-*      → P2
  medium-<service>-*    → P3
  low-<service>-*       → P4
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
INCIDENTS_TABLE        = os.environ.get("INCIDENTS_TABLE",  "sentinel-incidents")
EVENTS_STREAM          = os.environ.get("EVENTS_STREAM",    "sentinel-events")
AWS_REGION             = os.environ.get("AWS_REGION",       "us-east-1")
FLOCI_ENDPOINT         = os.environ.get("FLOCI_ENDPOINT",   "")

_SEVERITY_FROM_PREFIX = {
    "p1": "P1", "critical": "P1",
    "p2": "P2", "high":     "P2",
    "p3": "P3", "medium":   "P3",
    "p4": "P4", "low":      "P4",
}

_ERROR_RATE = {"P1": Decimal("0.18"), "P2": Decimal("0.07"), "P3": Decimal("0.03"), "P4": Decimal("0.01")}

_NOISE_WORDS = {"error", "rate", "high", "low", "latency", "cpu", "memory", "alarm", "alert", "threshold"}


def _boto_kwargs() -> dict:
    if FLOCI_ENDPOINT:
        return {"endpoint_url": FLOCI_ENDPOINT, "region_name": AWS_REGION}
    return {"region_name": AWS_REGION}


def _parse_alarm(alarm: dict) -> tuple[str, str, str]:
    """Return (service, severity, title) from a CloudWatch alarm dict."""
    name = alarm.get("AlarmName", "unknown-alarm")
    desc = alarm.get("AlarmDescription", "")

    parts  = re.split(r"[-_]", name.lower())
    sev    = _SEVERITY_FROM_PREFIX.get(parts[0], "P3") if parts else "P3"
    svc_parts = [p for p in parts[1:] if p not in _NOISE_WORDS] or ["unknown"]
    service = "-".join(svc_parts).strip("-") or "unknown"
    title   = desc or f"CloudWatch alarm: {name}"

    return service, sev, title


def _forward_to_dashboard(service: str, severity: str, title: str,
                           alarm: dict, iid: str) -> bool:
    """POST to the Sentinel dashboard /api/incidents/receive. Returns True on success."""
    if not SENTINEL_DASHBOARD_URL or not _HAS_REQUESTS:
        return False
    try:
        resp = _requests.post(
            f"{SENTINEL_DASHBOARD_URL}/api/incidents/receive",
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

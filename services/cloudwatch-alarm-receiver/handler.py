"""
CloudWatch Alarm → SNS → Lambda → Sentinel incident receiver.

SNS delivers CloudWatch alarm state-change notifications here.
The handler parses the alarm payload, derives service + severity from
the alarm name convention  (<severity>-<service>-*, e.g. p1-auth-service-error-rate),
writes a new incident directly to DynamoDB, and puts an event on the
Sentinel Kinesis events stream so the rest of the pipeline picks it up.

Alarm name convention  (case-insensitive):
  p1-<service>-*   → P1
  p2-<service>-*   → P2
  p3-<service>-*   → P3
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

INCIDENTS_TABLE  = os.environ.get("INCIDENTS_TABLE",  "sentinel-incidents")
EVENTS_STREAM    = os.environ.get("EVENTS_STREAM",    "sentinel-events")
AWS_REGION       = os.environ.get("AWS_REGION",       "us-east-1")
FLOCI_ENDPOINT   = os.environ.get("FLOCI_ENDPOINT",   "")

_SEVERITY_FROM_PREFIX = {
    "p1": "P1", "critical": "P1",
    "p2": "P2", "high":     "P2",
    "p3": "P3", "medium":   "P3",
    "p4": "P4", "low":      "P4",
}

_ERROR_RATE = {"P1": Decimal("0.18"), "P2": Decimal("0.07"), "P3": Decimal("0.03"), "P4": Decimal("0.01")}


def _boto_kwargs() -> dict:
    if FLOCI_ENDPOINT:
        return {"endpoint_url": FLOCI_ENDPOINT, "region_name": AWS_REGION}
    return {"region_name": AWS_REGION}


def _dynamo():
    return boto3.resource("dynamodb", **_boto_kwargs())


def _kinesis():
    return boto3.client("kinesis", **_boto_kwargs())


def _parse_alarm(alarm: dict) -> tuple[str, str, str]:
    """Return (service, severity, title) from a CloudWatch alarm dict."""
    name = alarm.get("AlarmName", "unknown-alarm")
    desc = alarm.get("AlarmDescription", "")

    # derive severity + service from alarm name prefix
    # e.g. "p1-auth-service-error-rate-high" → P1, auth-service
    parts  = re.split(r"[-_]", name.lower())
    sev    = _SEVERITY_FROM_PREFIX.get(parts[0], "P3") if parts else "P3"
    # service = everything between the severity prefix and any trailing metric suffix
    # strip trailing words that are common metric names
    _NOISE = {"error", "rate", "high", "low", "latency", "cpu", "memory", "alarm", "alert", "threshold"}
    svc_parts = [p for p in parts[1:] if p not in _NOISE] or ["unknown"]
    # rejoin and strip trailing hyphens
    service = "-".join(svc_parts).strip("-") or "unknown"

    title = desc or f"CloudWatch alarm: {name}"
    return service, sev, title


def _put_incident(incident: dict) -> None:
    _dynamo().Table(INCIDENTS_TABLE).put_item(Item=incident)


def _emit_event(incident_id: str, severity: str, service: str) -> None:
    event = {
        "event_type": "SEVERITY_ASSESSED",
        "aggregate_id": incident_id,
        "payload": {
            "incident_id":         incident_id,
            "severity":            severity,
            "rule_severity":       severity,
            "ml_severity":         severity,
            "degradation_trend":   "worsening" if severity in ("P1", "P2") else "stable",
            "affected_components": [service],
            "log_sample_size":     0,
            "source":              "cloudwatch-alarm",
            "analyzed_at":         datetime.now(timezone.utc).isoformat(),
        },
    }
    try:
        _kinesis().put_record(
            StreamName=EVENTS_STREAM,
            Data=json.dumps(event).encode(),
            PartitionKey=incident_id,
        )
    except Exception as exc:
        print(f"[cw-receiver] Kinesis put failed (non-fatal): {exc}")


def lambda_handler(event, context):
    processed = 0
    errors = []

    for record in event.get("Records", []):
        try:
            sns_msg = json.loads(record["Sns"]["Message"])
            # CloudWatch sends alarm state change when AlarmName is present
            if "AlarmName" not in sns_msg:
                continue
            # only act on ALARM state (ignore OK / INSUFFICIENT_DATA)
            if sns_msg.get("NewStateValue") != "ALARM":
                print(f"[cw-receiver] skipping non-ALARM state: {sns_msg.get('NewStateValue')}")
                continue

            service, severity, title = _parse_alarm(sns_msg)
            iid = f"cw-{uuid.uuid4().hex[:12]}"
            now = datetime.now(timezone.utc).isoformat()

            incident = {
                "incident_id": iid,
                "title":       title,
                "service":     service,
                "severity":    severity,
                "status":      "OPEN",
                "source":      "cloudwatch",
                "created_at":  now,
                "root_cause":  "⏳ AI analysis in progress…",
                "runbook":     {},
                "ai_generated": False,
                "rca_source":  "pending",
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
                    "alarm_name":   sns_msg.get("AlarmName"),
                    "alarm_region": sns_msg.get("Region", AWS_REGION),
                    "trigger":      sns_msg.get("Trigger", {}),
                    "ai_pending":   True,
                },
            }

            _put_incident(incident)
            print(f"[cw-receiver] wrote incident {iid} ({severity} {service})")

            _emit_event(iid, severity, service)
            processed += 1

        except Exception as exc:
            print(f"[cw-receiver] error processing record: {exc}")
            errors.append(str(exc))

    return {"processed": processed, "errors": errors}

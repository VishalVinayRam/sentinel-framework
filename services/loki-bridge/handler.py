"""
Loki bridge Lambda — receives Grafana/Loki AlertManager webhook payloads
and forwards them onto the Sentinel Kinesis alerts stream.

Loki alert rules fire to AlertManager which routes to this Lambda URL
via API Gateway. Works with:
  - Grafana unified alerting (Grafana 9+)
  - Standalone Loki + AlertManager
  - Grafana Cloud alerts
"""

import base64
import gzip
import json
import os
import uuid
from datetime import datetime, timezone

import boto3

ALERTS_STREAM = os.environ["ALERTS_STREAM"]
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

# AlertManager webhook body shape (same for Grafana unified alerting):
# {
#   "version": "4",
#   "groupKey": "...",
#   "status": "firing" | "resolved",
#   "receiver": "sentinel",
#   "alerts": [
#     {
#       "status": "firing",
#       "labels": {"alertname": "...", "service": "...", "severity": "..."},
#       "annotations": {"summary": "...", "description": "..."},
#       "startsAt": "2024-01-01T00:00:00Z",
#       "endsAt":   "0001-01-01T00:00:00Z",
#       "generatorURL": "http://loki:3100/..."
#     }
#   ]
# }


def handler(event, context):
    try:
        body = _parse_body(event)
    except Exception as e:
        return {"statusCode": 400, "body": json.dumps({"error": str(e)})}

    alerts = body.get("alerts", [])
    forwarded = 0
    for alert in alerts:
        try:
            _forward(alert, body.get("status", "firing"))
            forwarded += 1
        except Exception as e:
            print(f"[loki-bridge] failed to forward alert {alert}: {e}")

    return {"statusCode": 200, "body": json.dumps({"forwarded": forwarded})}


def _parse_body(event: dict) -> dict:
    raw = event.get("body", "{}")
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw)
        try:
            raw = gzip.decompress(raw)
        except Exception:
            pass
        raw = raw.decode("utf-8")
    return json.loads(raw or "{}")


def _forward(alert: dict, group_status: str) -> None:
    labels = alert.get("labels", {})
    annotations = alert.get("annotations", {})
    status = alert.get("status", group_status)

    service = (
        labels.get("service")
        or labels.get("job")
        or labels.get("app")
        or labels.get("alertname", "unknown")
    )

    # Map Loki severity label → Sentinel error_rate proxy so the validator
    # can apply its signal-threshold logic without knowing about Loki.
    loki_severity = labels.get("severity", "warning")
    error_rate = _severity_to_error_rate(loki_severity, status)

    payload = {
        "alert_id": str(uuid.uuid4()),
        "source": "loki",
        "service_name": service,
        "error_rate": error_rate,
        "p99_latency_ms": 0,
        "health_endpoint": "",
        "summary": annotations.get("summary", ""),
        "description": annotations.get("description", ""),
        "loki_labels": labels,
        "loki_annotations": annotations,
        "loki_generator_url": alert.get("generatorURL", ""),
        "status": status,
        "starts_at": alert.get("startsAt", ""),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    boto3.client("kinesis", region_name=AWS_REGION).put_record(
        StreamName=ALERTS_STREAM,
        Data=json.dumps(payload),
        PartitionKey=service,
    )
    print(f"[loki-bridge] forwarded {status} alert for {service} (severity={loki_severity})")


def _severity_to_error_rate(severity: str, status: str) -> float:
    if status == "resolved":
        return 0.0
    mapping = {
        "critical": 0.20,
        "error":    0.15,
        "warning":  0.10,
        "info":     0.02,
    }
    return mapping.get(severity.lower(), 0.10)

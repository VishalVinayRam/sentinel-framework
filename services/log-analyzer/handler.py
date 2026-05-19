import json
import base64
import os
import re
from decimal import Decimal
from datetime import datetime, timezone
from collections import Counter

import requests

import sys
sys.path.append("/var/task/shared")
from aws_clients import dynamodb, kinesis


INCIDENTS_TABLE = os.environ["INCIDENTS_TABLE"]
EVENTS_STREAM = os.environ["EVENTS_STREAM"]
KSERVE_ENDPOINT = os.environ["KSERVE_ENDPOINT"]

SEVERITY_RULES = [
    ({"FATAL", "CRITICAL", "OOM", "panic"}, "P1"),
    ({"ERROR", "Exception", "500", "timeout"}, "P2"),
    ({"WARN", "WARNING", "429", "retry"}, "P3"),
    ({"INFO", "DEBUG"}, "P4"),
]


def handler(event, context):
    for record in event.get("Records", []):
        log_batch = json.loads(base64.b64decode(record["kinesis"]["data"]))
        _analyze_log_batch(log_batch)
    return {"status": "ok"}


def _analyze_log_batch(batch: dict) -> None:
    incident_id = batch.get("incident_id")
    raw_logs = batch.get("logs", [])

    rule_severity = _classify_by_rules(raw_logs)
    ml_analysis = _classify_by_model(raw_logs, batch.get("service_name", "unknown"))
    final_severity = _merge_severity(rule_severity, ml_analysis.get("severity", "P4"))

    impact = _estimate_impact(raw_logs, ml_analysis)
    result = {
        "incident_id": incident_id,
        "severity": final_severity,
        "rule_severity": rule_severity,
        "ml_severity": ml_analysis.get("severity"),
        "impact_scope": impact,
        "degradation_trend": ml_analysis.get("degradation_trend", "stable"),
        "affected_components": ml_analysis.get("affected_components", []),
        "log_sample_size": len(raw_logs),
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    }

    _update_incident(incident_id, result)
    _publish_severity_event(incident_id, result)


def _classify_by_rules(logs: list) -> str:
    text = " ".join(str(l) for l in logs)
    for keywords, severity in SEVERITY_RULES:
        if any(kw in text for kw in keywords):
            return severity
    return "P4"


def _classify_by_model(logs: list, service: str) -> dict:
    sample = logs[:50]
    prompt = f"""Analyze these logs from service '{service}' and return JSON with:
- severity: P1/P2/P3/P4 (P1=critical outage, P2=major degradation, P3=minor issue, P4=informational)
- degradation_trend: improving/stable/worsening
- affected_components: list of affected system components
- users_impacted: estimated percentage
- summary: one sentence

Logs:
{json.dumps(sample, indent=2)[:3000]}"""

    try:
        resp = requests.post(
            f"{KSERVE_ENDPOINT}/v1/models/phi-3-lite:predict",
            json={"inputs": [{"name": "text_input", "shape": [1], "datatype": "BYTES", "data": [prompt]}]},
            timeout=30,
        )
        resp.raise_for_status()
        raw = resp.json()["outputs"][0]["data"][0]
        return json.loads(raw)
    except Exception:
        return {"severity": "P3", "degradation_trend": "stable", "affected_components": []}


def _merge_severity(rule: str, ml: str) -> str:
    order = ["P1", "P2", "P3", "P4"]
    return rule if order.index(rule) <= order.index(ml) else ml


def _estimate_impact(logs: list, ml_analysis: dict) -> dict:
    error_count = sum(1 for l in logs if re.search(r"error|exception|500", str(l), re.I))
    # DynamoDB requires Decimal for all numeric types — no plain floats allowed
    return {
        "error_rate_in_sample": Decimal(str(round(error_count / max(len(logs), 1), 3))),
        "users_impacted_pct": Decimal(str(ml_analysis.get("users_impacted", 0))),
        "summary": ml_analysis.get("summary", ""),
    }


def _update_incident(incident_id: str, result: dict) -> None:
    if not incident_id:
        return
    table = dynamodb().Table(INCIDENTS_TABLE)
    table.update_item(
        Key={"incident_id": incident_id},
        UpdateExpression="SET severity = :s, impact_scope = :i, degradation_trend = :d, log_analysis = :l",
        ExpressionAttributeValues={
            ":s": result["severity"],
            ":i": result["impact_scope"],
            ":d": result["degradation_trend"],
            ":l": result,
        },
    )


class _DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def _publish_severity_event(incident_id: str, result: dict) -> None:
    if not incident_id:
        return
    kinesis_client = kinesis()
    kinesis_client.put_record(
        StreamName=EVENTS_STREAM,
        Data=json.dumps({
            "event_type": "SEVERITY_CLASSIFIED",
            "aggregate_id": incident_id,
            "payload": result,
            "timestamp": result["analyzed_at"],
        }, cls=_DecimalEncoder),
        PartitionKey=incident_id,
    )

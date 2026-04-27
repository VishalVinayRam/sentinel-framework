import json
import os
import uuid
from datetime import datetime, timezone

import requests

import sys
sys.path.append("/var/task/shared")
from aws_clients import dynamodb, kinesis


INCIDENTS_TABLE = os.environ["INCIDENTS_TABLE"]
EVENTS_STREAM = os.environ["EVENTS_STREAM"]
KSERVE_ENDPOINT = os.environ["KSERVE_ENDPOINT"]
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
GITHUB_REPO = os.environ["GITHUB_REPO"]
RAG_ENDPOINT = os.environ["RAG_ENDPOINT"]


def handler(event, context):
    """Entry point for Step Functions. Each step calls this with a different action."""
    action = event.get("action")
    incident_id = event.get("incident_id")
    state = event.get("state", {})

    dispatch = {
        "fetch_recent_changes": _fetch_recent_changes,
        "query_rag": _query_rag,
        "analyze_root_cause": _analyze_root_cause,
        "generate_runbook": _generate_runbook,
        "publish_report": _publish_report,
    }

    if action not in dispatch:
        raise ValueError(f"Unknown action: {action}")

    result = dispatch[action](incident_id, state)
    return {**state, **result, "incident_id": incident_id, "action_completed": action}


def _fetch_recent_changes(incident_id: str, state: dict) -> dict:
    resp = requests.get(
        f"https://api.github.com/repos/{GITHUB_REPO}/commits",
        headers={"Authorization": f"token {GITHUB_TOKEN}"},
        params={"per_page": 10},
    )
    commits = resp.json() if resp.ok else []
    return {
        "recent_commits": [
            {"sha": c["sha"][:8], "message": c["commit"]["message"][:100], "author": c["commit"]["author"]["name"]}
            for c in commits
        ]
    }


def _query_rag(incident_id: str, state: dict) -> dict:
    incident = _get_incident(incident_id)
    query = f"{incident.get('service_name', '')} {incident.get('severity', '')} {incident.get('impact_scope', {}).get('summary', '')}"

    resp = requests.post(
        f"{RAG_ENDPOINT}/query",
        json={"query": query, "top_k": 5},
        timeout=15,
    )
    similar = resp.json().get("results", []) if resp.ok else []
    return {"similar_past_incidents": similar}


def _analyze_root_cause(incident_id: str, state: dict) -> dict:
    incident = _get_incident(incident_id)
    commits = state.get("recent_commits", [])
    similar = state.get("similar_past_incidents", [])

    prompt = f"""You are a senior SRE analyzing a production incident. Identify the root cause.

Incident:
- Service: {incident.get('service_name')}
- Severity: {incident.get('severity')}
- Impact: {json.dumps(incident.get('impact_scope', {}), indent=2)}
- Log Analysis: {json.dumps(incident.get('log_analysis', {}), indent=2)}

Recent commits deployed before incident:
{json.dumps(commits, indent=2)}

Similar past incidents resolved with:
{json.dumps(similar, indent=2)}

Return JSON with:
- root_cause: most likely cause (one sentence)
- contributing_factors: list of contributing factors
- missed_edge_case: which edge case was missed in code/design
- confidence: high/medium/low
- affected_commit: sha of likely culprit commit if identifiable"""

    resp = requests.post(
        f"{KSERVE_ENDPOINT}/v1/models/phi-3-lite:predict",
        json={"inputs": [{"name": "text_input", "shape": [1], "datatype": "BYTES", "data": [prompt]}]},
        timeout=60,
    )
    raw = resp.json()["outputs"][0]["data"][0]
    try:
        analysis = json.loads(raw)
    except Exception:
        analysis = {"root_cause": raw, "confidence": "low", "contributing_factors": [], "missed_edge_case": ""}

    return {"root_cause_analysis": analysis}


def _generate_runbook(incident_id: str, state: dict) -> dict:
    analysis = state.get("root_cause_analysis", {})
    incident = _get_incident(incident_id)

    prompt = f"""Generate a step-by-step incident runbook for an on-call engineer.

Root cause: {analysis.get('root_cause')}
Missed edge case: {analysis.get('missed_edge_case')}
Severity: {incident.get('severity')}

Return JSON with:
- immediate_actions: list of immediate mitigation steps (numbered)
- rollback_steps: how to rollback if needed
- monitoring_checks: what to watch after mitigation
- prevention: how to prevent this in future (code fix + process fix)
- estimated_resolution_time: in minutes"""

    resp = requests.post(
        f"{KSERVE_ENDPOINT}/v1/models/phi-3-lite:predict",
        json={"inputs": [{"name": "text_input", "shape": [1], "datatype": "BYTES", "data": [prompt]}]},
        timeout=60,
    )
    raw = resp.json()["outputs"][0]["data"][0]
    try:
        runbook = json.loads(raw)
    except Exception:
        runbook = {"immediate_actions": [raw], "rollback_steps": [], "monitoring_checks": [], "prevention": ""}

    return {"runbook": runbook}


def _publish_report(incident_id: str, state: dict) -> dict:
    incident = _get_incident(incident_id)
    report = {
        "report_id": str(uuid.uuid4()),
        "incident_id": incident_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "severity": incident.get("severity"),
        "root_cause_analysis": state.get("root_cause_analysis", {}),
        "runbook": state.get("runbook", {}),
        "recent_commits": state.get("recent_commits", []),
        "similar_incidents": state.get("similar_past_incidents", []),
    }

    table = dynamodb().Table(INCIDENTS_TABLE)
    table.update_item(
        Key={"incident_id": incident_id},
        UpdateExpression="SET incident_report = :r, status = :s",
        ExpressionAttributeValues={":r": report, ":s": "REPORT_READY"},
    )

    kinesis_client = kinesis()
    kinesis_client.put_record(
        StreamName=EVENTS_STREAM,
        Data=json.dumps({"event_type": "REPORT_PUBLISHED", "aggregate_id": incident_id, "payload": report}),
        PartitionKey=incident_id,
    )

    return {"report_id": report["report_id"]}


def _get_incident(incident_id: str) -> dict:
    table = dynamodb().Table(INCIDENTS_TABLE)
    resp = table.get_item(Key={"incident_id": incident_id})
    return resp.get("Item", {})

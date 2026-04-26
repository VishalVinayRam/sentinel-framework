import json
import os
import uuid
from datetime import datetime, timezone

import requests
import boto3

import sys
sys.path.append("/var/task/shared")
from aws_clients import dynamodb, sqs


VALIDATION_RESULTS_TABLE = os.environ["VALIDATION_RESULTS_TABLE"]
VALIDATION_JOBS_QUEUE_URL = os.environ["VALIDATION_JOBS_QUEUE_URL"]
EVENTS_QUEUE_URL = os.environ["EVENTS_QUEUE_URL"]

# Minimum number of independent signals before declaring a real incident
SIGNAL_THRESHOLD = 2


def handler(event, context):
    """Consumes from Kinesis alerts stream. Validates each alert is a real failure."""
    results = []
    for record in event.get("Records", []):
        import base64
        payload = json.loads(base64.b64decode(record["kinesis"]["data"]))
        result = _validate_alert(payload)
        results.append(result)
    return {"processed": len(results)}


def _validate_alert(alert: dict) -> dict:
    alert_id = alert.get("alert_id", str(uuid.uuid4()))
    service = alert.get("service_name", "unknown")
    health_url = alert.get("health_endpoint", "")

    signals = []

    # Signal 1: health endpoint check
    if health_url:
        signals.append(_check_health_endpoint(health_url))

    # Signal 2: dispatch smoke test job and wait for result
    smoke_result = _dispatch_smoke_test(alert_id, service, alert)
    signals.append(smoke_result)

    # Signal 3: check if multiple metrics agree
    signals.append(_check_metric_consensus(alert))

    confirmed_failures = [s for s in signals if s is False]
    is_real = len(confirmed_failures) >= SIGNAL_THRESHOLD

    result = {
        "alert_id": alert_id,
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "is_real_incident": str(is_real).lower(),
        "signals_checked": len(signals),
        "signals_failed": len(confirmed_failures),
        "service_name": service,
        "original_alert": alert,
    }

    _store_result(result)

    if is_real:
        _publish_confirmed_incident(result)

    return result


def _check_health_endpoint(url: str) -> bool:
    """Returns True if healthy, False if unhealthy."""
    try:
        resp = requests.get(url, timeout=5)
        return resp.status_code < 500
    except Exception:
        return False


def _dispatch_smoke_test(alert_id: str, service: str, alert: dict) -> bool:
    """Sends a smoke test job to SQS and returns a mock result for now."""
    sqs_client = sqs()
    sqs_client.send_message(
        QueueUrl=VALIDATION_JOBS_QUEUE_URL,
        MessageBody=json.dumps({
            "alert_id": alert_id,
            "service": service,
            "test_type": "smoke",
            "alert_context": alert,
        }),
        MessageGroupId=service if VALIDATION_JOBS_QUEUE_URL.endswith(".fifo") else None,
    )
    # Synchronous smoke test result would come from a separate executor
    # For now we check the alert's error rate as a proxy
    return alert.get("error_rate", 0) < 0.05


def _check_metric_consensus(alert: dict) -> bool:
    """Returns True if metrics suggest service is healthy, False otherwise."""
    error_rate = alert.get("error_rate", 0)
    p99_latency = alert.get("p99_latency_ms", 0)
    return not (error_rate > 0.05 or p99_latency > 2000)


def _store_result(result: dict) -> None:
    table = dynamodb().Table(VALIDATION_RESULTS_TABLE)
    table.put_item(Item=result)


def _publish_confirmed_incident(result: dict) -> None:
    sqs_client = sqs()
    sqs_client.send_message(
        QueueUrl=EVENTS_QUEUE_URL,
        MessageBody=json.dumps({
            "event_type": "INCIDENT_CONFIRMED",
            "aggregate_id": result["alert_id"],
            "payload": result,
            "timestamp": result["validated_at"],
        }),
    )

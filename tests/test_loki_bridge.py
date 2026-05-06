"""Tests for the loki-bridge Lambda handler."""

import base64
import gzip
import json
import sys
import os
from unittest.mock import MagicMock, patch

# The handler lives outside the sentinel package; add it to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "loki-bridge"))

os.environ.setdefault("ALERTS_STREAM", "sentinel-alerts")
os.environ.setdefault("AWS_REGION", "us-east-1")

import handler as loki_handler


def _make_event(body: dict, base64_encoded: bool = False) -> dict:
    raw = json.dumps(body)
    if base64_encoded:
        return {"body": base64.b64encode(raw.encode()).decode(), "isBase64Encoded": True}
    return {"body": raw, "isBase64Encoded": False}


def _alertmanager_payload(status="firing", severity="warning", service="my-svc"):
    return {
        "version": "4",
        "status": status,
        "receiver": "sentinel",
        "alerts": [
            {
                "status": status,
                "labels": {
                    "alertname": "HighErrorRate",
                    "service": service,
                    "severity": severity,
                    "sentinel": "true",
                },
                "annotations": {
                    "summary": f"Error rate spike in {service}",
                    "description": "Too many errors",
                },
                "startsAt": "2025-01-01T00:00:00Z",
                "endsAt": "0001-01-01T00:00:00Z",
                "generatorURL": "http://loki:3100/query",
            }
        ],
    }


@patch("handler.boto3")
def test_firing_alert_forwarded(mock_boto3):
    mock_kinesis = MagicMock()
    mock_boto3.client.return_value = mock_kinesis

    event = _make_event(_alertmanager_payload("firing", "critical", "auth-service"))
    resp = loki_handler.handler(event, {})

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["forwarded"] == 1

    mock_kinesis.put_record.assert_called_once()
    call_kwargs = mock_kinesis.put_record.call_args[1]
    assert call_kwargs["StreamName"] == "sentinel-alerts"
    payload = json.loads(call_kwargs["Data"])
    assert payload["source"] == "loki"
    assert payload["service_name"] == "auth-service"
    assert payload["status"] == "firing"


@patch("handler.boto3")
def test_resolved_alert_zero_error_rate(mock_boto3):
    mock_kinesis = MagicMock()
    mock_boto3.client.return_value = mock_kinesis

    event = _make_event(_alertmanager_payload("resolved", "warning", "search"))
    loki_handler.handler(event, {})

    payload = json.loads(mock_kinesis.put_record.call_args[1]["Data"])
    assert payload["error_rate"] == 0.0
    assert payload["status"] == "resolved"


@patch("handler.boto3")
def test_severity_maps_to_error_rate(mock_boto3):
    mock_kinesis = MagicMock()
    mock_boto3.client.return_value = mock_kinesis

    for severity, expected in [("critical", 0.20), ("error", 0.15), ("warning", 0.10), ("info", 0.02)]:
        mock_kinesis.reset_mock()
        event = _make_event(_alertmanager_payload("firing", severity, "svc"))
        loki_handler.handler(event, {})
        payload = json.loads(mock_kinesis.put_record.call_args[1]["Data"])
        assert payload["error_rate"] == expected, f"wrong rate for severity={severity}"


@patch("handler.boto3")
def test_multiple_alerts_in_batch(mock_boto3):
    mock_kinesis = MagicMock()
    mock_boto3.client.return_value = mock_kinesis

    body = {
        "status": "firing",
        "alerts": [
            _alertmanager_payload()["alerts"][0],
            _alertmanager_payload(service="payments")["alerts"][0],
        ],
    }
    event = _make_event(body)
    resp = loki_handler.handler(event, {})
    assert json.loads(resp["body"])["forwarded"] == 2
    assert mock_kinesis.put_record.call_count == 2


@patch("handler.boto3")
def test_base64_encoded_body(mock_boto3):
    mock_kinesis = MagicMock()
    mock_boto3.client.return_value = mock_kinesis

    event = _make_event(_alertmanager_payload(), base64_encoded=True)
    resp = loki_handler.handler(event, {})
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["forwarded"] == 1


def test_empty_body_returns_200():
    resp = loki_handler.handler({"body": "{}", "isBase64Encoded": False}, {})
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["forwarded"] == 0


def test_malformed_body_returns_400():
    resp = loki_handler.handler({"body": "not json", "isBase64Encoded": False}, {})
    assert resp["statusCode"] == 400


@patch("handler.boto3")
def test_service_label_fallback_chain(mock_boto3):
    mock_kinesis = MagicMock()
    mock_boto3.client.return_value = mock_kinesis

    # no 'service' label — should fall back to 'job'
    body = {
        "status": "firing",
        "alerts": [{
            "status": "firing",
            "labels": {"job": "nginx", "alertname": "HighErrorRate"},
            "annotations": {},
            "startsAt": "2025-01-01T00:00:00Z",
        }],
    }
    loki_handler.handler({"body": json.dumps(body), "isBase64Encoded": False}, {})
    payload = json.loads(mock_kinesis.put_record.call_args[1]["Data"])
    assert payload["service_name"] == "nginx"

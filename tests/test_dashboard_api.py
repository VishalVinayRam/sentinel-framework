"""Unit tests for the Sentinel dashboard FastAPI endpoints.

DynamoDB calls are mocked via unittest.mock so no Floci/AWS is required.
"""
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Ensure sentinel package is importable from repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Set SENTINEL_API_KEY before importing the app so auth is enforced in tests
TEST_API_KEY = "test-api-key-123"
os.environ["SENTINEL_API_KEY"] = TEST_API_KEY

from services.dashboard.api import app  # noqa: E402

client = TestClient(app, raise_server_exceptions=False)

AUTH = {"X-API-Key": TEST_API_KEY}

# ── helpers ──────────────────────────────────────────────────────────────────

def _incident(incident_id="inc-001", severity="P1", status="OPEN", service="auth-service"):
    return {
        "incident_id":  incident_id,
        "title":        f"{service} — test incident",
        "service":      service,
        "severity":     severity,
        "status":       status,
        "created_at":   "2026-05-20T10:00:00+00:00",
        "root_cause":   "test root cause",
        "runbook":      {},
        "log_analysis": {"severity": severity, "degradation_trend": "stable",
                         "affected_components": [service], "log_sample_size": 0,
                         "error_rate_in_sample": Decimal("0.05")},
        "impact_scope": {"error_rate_in_sample": Decimal("0.05"),
                         "users_impacted_pct": 5, "summary": "test"},
        "metadata":     {"source": "test", "ai_pending": False},
    }


def _mock_table(items=None, get_item_result=None):
    """Return a mock DynamoDB Table with scan/get_item/put_item/update_item stubbed."""
    tbl = MagicMock()
    tbl.scan.return_value        = {"Items": items or []}
    tbl.get_item.return_value    = {"Item": get_item_result} if get_item_result else {}
    tbl.put_item.return_value    = {}
    tbl.update_item.return_value = {}
    return tbl


# ── /health ───────────────────────────────────────────────────────────────────

def test_health_no_auth_returns_200():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ── auth ──────────────────────────────────────────────────────────────────────

def test_missing_api_key_returns_401():
    r = client.get("/api/incidents")
    assert r.status_code == 401
    assert r.json()["detail"]["error"] == "Unauthorized"


def test_wrong_api_key_returns_401():
    r = client.get("/api/incidents", headers={"X-API-Key": "wrong-key"})
    assert r.status_code == 401


def test_correct_api_key_passes():
    with patch("services.dashboard.api._dynamo") as mock_dynamo:
        mock_dynamo.return_value.Table.return_value = _mock_table([])
        r = client.get("/api/incidents", headers=AUTH)
    assert r.status_code == 200


# ── GET /api/incidents ────────────────────────────────────────────────────────

def test_list_incidents_returns_items():
    items = [_incident("inc-001", "P1"), _incident("inc-002", "P2")]
    with patch("services.dashboard.api._dynamo") as mock_dynamo:
        mock_dynamo.return_value.Table.return_value = _mock_table(items)
        r = client.get("/api/incidents", headers=AUTH)
    assert r.status_code == 200
    assert len(r.json()) == 2


def test_list_incidents_filters_by_status():
    items = [_incident("a", status="OPEN"), _incident("b", status="RESOLVED")]
    with patch("services.dashboard.api._dynamo") as mock_dynamo:
        mock_dynamo.return_value.Table.return_value = _mock_table(items)
        r = client.get("/api/incidents?status=OPEN", headers=AUTH)
    data = r.json()
    assert all(i["status"] == "OPEN" for i in data)


def test_list_incidents_filters_by_severity():
    items = [_incident("a", severity="P1"), _incident("b", severity="P2")]
    with patch("services.dashboard.api._dynamo") as mock_dynamo:
        mock_dynamo.return_value.Table.return_value = _mock_table(items)
        r = client.get("/api/incidents?severity=P1", headers=AUTH)
    data = r.json()
    assert all(i["severity"] == "P1" for i in data)


def test_list_incidents_dynamo_error_returns_503():
    with patch("services.dashboard.api._dynamo") as mock_dynamo:
        mock_dynamo.return_value.Table.side_effect = Exception("DynamoDB unreachable")
        r = client.get("/api/incidents", headers=AUTH)
    assert r.status_code == 503
    assert r.json()["detail"]["error"] == "Service Unavailable"


# ── GET /api/incidents/{id} ───────────────────────────────────────────────────

def test_get_incident_found_returns_200():
    inc = _incident("inc-001")
    with patch("services.dashboard.api._dynamo") as mock_dynamo:
        mock_dynamo.return_value.Table.return_value = _mock_table(get_item_result=inc)
        r = client.get("/api/incidents/inc-001", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["incident_id"] == "inc-001"


def test_get_incident_not_found_returns_404():
    with patch("services.dashboard.api._dynamo") as mock_dynamo:
        mock_dynamo.return_value.Table.return_value = _mock_table(get_item_result=None)
        r = client.get("/api/incidents/nonexistent", headers=AUTH)
    assert r.status_code == 404


# ── GET /api/stats ────────────────────────────────────────────────────────────

def test_get_stats_returns_counts():
    items = [
        _incident("a", severity="P1", status="OPEN"),
        _incident("b", severity="P2", status="OPEN"),
        _incident("c", severity="P1", status="RESOLVED"),
    ]
    with patch("services.dashboard.api._dynamo") as mock_dynamo:
        tbl = _mock_table(items)
        mock_dynamo.return_value.Table.return_value = tbl
        r = client.get("/api/stats", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["total_incidents"] == 3
    assert body["open_incidents"] == 2
    assert body["severity_breakdown"]["P1"] == 1   # only open P1s counted


def test_get_stats_dynamo_error_returns_503():
    with patch("services.dashboard.api._dynamo") as mock_dynamo:
        mock_dynamo.return_value.Table.side_effect = Exception("timeout")
        r = client.get("/api/stats", headers=AUTH)
    assert r.status_code == 503


# ── POST /api/incidents/receive (validation) ──────────────────────────────────

def test_receive_valid_payload_returns_202():
    with patch("services.dashboard.api._dynamo") as mock_dynamo, \
         patch("services.dashboard.api._RCA_EXECUTOR") as mock_pool:
        mock_dynamo.return_value.Table.return_value = _mock_table()
        r = client.post("/api/incidents/receive", headers=AUTH,
                        json={"service": "auth-service", "severity": "P1", "title": "DB down"})
    assert r.status_code == 200
    body = r.json()
    assert body["severity"] == "P1"
    assert body["service"] == "auth-service"
    assert body["ai_pending"] is True


def test_receive_invalid_severity_returns_422():
    r = client.post("/api/incidents/receive", headers=AUTH,
                    json={"service": "auth-service", "severity": "P9"})
    assert r.status_code == 422


def test_receive_missing_service_returns_422():
    r = client.post("/api/incidents/receive", headers=AUTH,
                    json={"severity": "P1"})
    assert r.status_code == 422


def test_receive_service_too_long_returns_422():
    r = client.post("/api/incidents/receive", headers=AUTH,
                    json={"service": "x" * 129, "severity": "P1"})
    assert r.status_code == 422


def test_receive_title_too_long_returns_422():
    r = client.post("/api/incidents/receive", headers=AUTH,
                    json={"service": "auth", "severity": "P1", "title": "t" * 513})
    assert r.status_code == 422


# ── Idempotency (#24) ─────────────────────────────────────────────────────────

def test_receive_duplicate_incident_id_returns_existing():
    existing = _incident("existing-id", severity="P2")
    with patch("services.dashboard.api._dynamo") as mock_dynamo:
        tbl = _mock_table(get_item_result=existing)
        mock_dynamo.return_value.Table.return_value = tbl
        r = client.post("/api/incidents/receive", headers=AUTH,
                        json={"service": "auth", "severity": "P1",
                              "incident_id": "existing-id"})
    assert r.status_code == 200
    # Must return the existing record, not create a new one
    assert r.json()["incident_id"] == "existing-id"
    assert r.json()["severity"] == "P2"          # original severity preserved
    tbl.put_item.assert_not_called()              # no new write


# ── POST /api/demo/fire ────────────────────────────────────────────────────────

def test_fire_demo_valid():
    with patch("services.dashboard.api._dynamo") as mock_dynamo, \
         patch("services.dashboard.api._RCA_EXECUTOR"):
        mock_dynamo.return_value.Table.return_value = _mock_table()
        r = client.post("/api/demo/fire", headers=AUTH,
                        json={"severity": "P2", "service": "payment-service"})
    assert r.status_code == 200
    assert r.json()["service"] == "payment-service"


def test_fire_demo_invalid_severity_returns_422():
    r = client.post("/api/demo/fire", headers=AUTH,
                    json={"severity": "CRITICAL", "service": "svc"})
    assert r.status_code == 422


# ── POST /api/incidents/{id}/resolve ─────────────────────────────────────────

def test_resolve_incident_success():
    with patch("services.dashboard.api._dynamo") as mock_dynamo:
        mock_dynamo.return_value.Table.return_value = _mock_table()
        r = client.post("/api/incidents/inc-001/resolve", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["status"] == "resolved"


def test_resolve_incident_requires_auth():
    r = client.post("/api/incidents/inc-001/resolve")
    assert r.status_code == 401


# ── GET /api/validations ──────────────────────────────────────────────────────

def test_list_validations_returns_items():
    rows = [{"validation_id": "v1", "validated_at": "2026-05-20T10:00:00"}]
    with patch("services.dashboard.api._dynamo") as mock_dynamo:
        mock_dynamo.return_value.Table.return_value = _mock_table(rows)
        r = client.get("/api/validations", headers=AUTH)
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_list_validations_dynamo_error_returns_503():
    with patch("services.dashboard.api._dynamo") as mock_dynamo:
        mock_dynamo.return_value.Table.side_effect = Exception("down")
        r = client.get("/api/validations", headers=AUTH)
    assert r.status_code == 503

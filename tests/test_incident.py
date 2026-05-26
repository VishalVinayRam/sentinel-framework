from datetime import datetime
from sentinel.core.incident import Incident
from sentinel.core.severity import Severity


def test_incident_defaults():
    inc = Incident(service_name="auth-service", severity=Severity.P2)
    assert inc.service_name == "auth-service"
    assert inc.severity == Severity.P2
    assert inc.status == "OPEN"
    assert inc.root_cause == ""
    assert inc.runbook == {}
    assert inc.alert_id is not None
    assert len(inc.alert_id) == 36  # uuid4 format


def test_incident_unique_ids():
    a = Incident(service_name="svc", severity=Severity.P3)
    b = Incident(service_name="svc", severity=Severity.P3)
    assert a.alert_id != b.alert_id


def test_to_dict_round_trip():
    inc = Incident(
        service_name="payments",
        severity=Severity.P1,
        status="OPEN",
        root_cause="database connection pool exhausted",
        runbook={"step1": "restart pods"},
        metadata={"region": "us-east-1"},
    )
    d = inc.to_dict()
    assert d["service_name"] == "payments"
    assert d["severity"] == "P1"
    assert d["status"] == "OPEN"
    assert d["root_cause"] == "database connection pool exhausted"
    assert d["runbook"] == {"step1": "restart pods"}
    assert d["metadata"] == {"region": "us-east-1"}


def test_from_dict_restores_incident():
    d = {
        "alert_id": "test-id-123",
        "service_name": "search",
        "severity": "P3",
        "created_at": "2025-01-01T00:00:00+00:00",
        "status": "RESOLVED",
        "root_cause": "cache miss spike",
        "runbook": {},
        "impact_scope": {},
        "log_analysis": {},
        "incident_report": {},
        "metadata": {},
    }
    inc = Incident.from_dict(d)
    assert inc.alert_id == "test-id-123"
    assert inc.service_name == "search"
    assert inc.severity == Severity.P3
    assert inc.status == "RESOLVED"
    assert inc.root_cause == "cache miss spike"


def test_serialization_preserves_severity_string():
    inc = Incident(service_name="svc", severity=Severity.P4)
    d = inc.to_dict()
    assert isinstance(d["severity"], str)
    assert d["severity"] == "P4"


def test_from_dict_missing_created_at_uses_now():
    d = {
        "service_name": "svc",
        "severity": "P2",
    }
    inc = Incident.from_dict(d)
    assert inc.created_at is not None
    assert inc.created_at.tzinfo is not None


def test_to_dict_created_at_is_iso_string():
    inc = Incident(service_name="svc", severity=Severity.P2)
    d = inc.to_dict()
    parsed = datetime.fromisoformat(d["created_at"])
    assert parsed.tzinfo is not None

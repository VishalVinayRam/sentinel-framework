"""Unit tests for all six Sentinel Lambda handlers.

All AWS and HTTP calls are mocked — no real infrastructure required.
"""
import base64
import gzip
import hashlib
import hmac
import importlib.util
import json
import os
import sys
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

# ── Environment setup (must happen before any handler import) ──────────────
os.environ.setdefault("INCIDENTS_TABLE",           "sentinel-incidents")
os.environ.setdefault("EVENTS_STREAM",             "sentinel-events")
os.environ.setdefault("ALERTS_STREAM",             "sentinel-alerts")
os.environ.setdefault("VALIDATION_RESULTS_TABLE",  "sentinel-validation-results")
os.environ.setdefault("VALIDATION_JOBS_QUEUE_URL", "http://sqs.local/validation-jobs")
os.environ.setdefault("EVENTS_QUEUE_URL",          "http://sqs.local/events")
os.environ.setdefault("KSERVE_ENDPOINT",           "http://kserve-test:8080")
os.environ.setdefault("GITHUB_TOKEN",              "gh-test-token")
os.environ.setdefault("GITHUB_REPO",               "test-org/test-repo")
os.environ.setdefault("RAG_ENDPOINT",              "http://rag-test:8000")
os.environ.setdefault("GITHUB_WEBHOOK_SECRET",     "test-webhook-secret")
os.environ.setdefault("PR_REVIEWS_TABLE",          "sentinel-pr-reviews")
os.environ.setdefault("CODEBASE_BUCKET",           "test-bucket")

# Stub aws_clients before any handler that imports it is loaded
_mock_aws = MagicMock()
sys.modules["aws_clients"] = _mock_aws

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(service: str):
    """Load services/<service>/handler.py as a uniquely-named module."""
    name = f"_hndl_{service.replace('-', '_')}"
    if name in sys.modules:
        return sys.modules[name]
    path = os.path.join(_REPO, "services", service, "handler.py")
    spec = importlib.util.spec_from_file_location(name, path)
    mod  = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_cw  = _load("cloudwatch-alarm-receiver")
_la  = _load("log-analyzer")
_lb  = _load("loki-bridge")
_val = _load("validator")
_rca = _load("root-cause-agent")
_pr  = _load("pr-security-agent")


# ── shared helpers ──────────────────────────────────────────────────────────

def _kinesis_record(payload: dict) -> dict:
    data = base64.b64encode(json.dumps(payload).encode()).decode()
    return {"kinesis": {"data": data}}


def _sns_record(alarm: dict) -> dict:
    return {"Sns": {"Message": json.dumps(alarm)}}


def _alarm(name="p1-auth-service-error-rate", state="ALARM", desc="") -> dict:
    return {
        "AlarmName":      name,
        "AlarmDescription": desc,
        "NewStateValue":  state,
        "Region":         "us-east-1",
        "Trigger":        {"Namespace": "AWS/ECS", "MetricName": "CPUUtilization"},
    }


def _make_sig(body: str, secret: str = "test-webhook-secret") -> str:
    return "sha256=" + hmac.new(
        secret.encode(), body.encode(), hashlib.sha256
    ).hexdigest()


# ════════════════════════════════════════════════════════════════════════════
# CloudWatch alarm receiver
# ════════════════════════════════════════════════════════════════════════════

class TestCloudWatchAlarmReceiver:

    def test_parse_alarm_p1_prefix(self):
        _, sev, _ = _cw._parse_alarm({"AlarmName": "p1-auth-service-cpu"})
        assert sev == "P1"

    def test_parse_alarm_critical_prefix(self):
        _, sev, _ = _cw._parse_alarm({"AlarmName": "critical-payment-down"})
        assert sev == "P1"

    def test_parse_alarm_p2_prefix(self):
        _, sev, _ = _cw._parse_alarm({"AlarmName": "p2-order-service"})
        assert sev == "P2"

    def test_parse_alarm_high_prefix(self):
        _, sev, _ = _cw._parse_alarm({"AlarmName": "high-db-latency"})
        assert sev == "P2"

    def test_parse_alarm_medium_prefix(self):
        _, sev, _ = _cw._parse_alarm({"AlarmName": "medium-cache-miss"})
        assert sev == "P3"

    def test_parse_alarm_unknown_prefix_defaults_to_p3(self):
        _, sev, _ = _cw._parse_alarm({"AlarmName": "custom-thing"})
        assert sev == "P3"

    def test_parse_alarm_uses_description_as_title(self):
        _, _, title = _cw._parse_alarm({
            "AlarmName": "p1-svc", "AlarmDescription": "DB is down"
        })
        assert title == "DB is down"

    def test_parse_alarm_falls_back_to_alarm_name_when_no_description(self):
        _, _, title = _cw._parse_alarm({"AlarmName": "p1-auth-alert"})
        assert "p1-auth-alert" in title

    def test_parse_alarm_filters_noise_words_from_service(self):
        svc, _, _ = _cw._parse_alarm({"AlarmName": "p1-auth-error-rate-high"})
        for word in ("error", "rate", "high"):
            assert word not in svc

    def test_parse_alarm_extracts_service_name(self):
        svc, _, _ = _cw._parse_alarm({"AlarmName": "p1-payment-service"})
        assert "payment" in svc

    def test_lambda_handler_skips_ok_state(self):
        result = _cw.lambda_handler(
            {"Records": [_sns_record(_alarm(state="OK"))]}, {}
        )
        assert result["processed"] == 0

    def test_lambda_handler_skips_insufficient_data_state(self):
        result = _cw.lambda_handler(
            {"Records": [_sns_record(_alarm(state="INSUFFICIENT_DATA"))]}, {}
        )
        assert result["processed"] == 0

    def test_lambda_handler_skips_record_without_alarm_name(self):
        result = _cw.lambda_handler(
            {"Records": [_sns_record({"NewStateValue": "ALARM"})]}, {}
        )
        assert result["processed"] == 0

    def test_lambda_handler_writes_directly_when_no_dashboard_url(self):
        with patch.object(_cw, "SENTINEL_DASHBOARD_URL", ""), \
             patch("boto3.resource") as mock_res, \
             patch("boto3.client") as mock_cli:
            mock_res.return_value.Table.return_value.put_item.return_value = {}
            mock_cli.return_value.put_record.return_value = {}
            result = _cw.lambda_handler(
                {"Records": [_sns_record(_alarm())]}, {}
            )
        assert result["processed"] == 1
        mock_res.return_value.Table.return_value.put_item.assert_called_once()

    def test_lambda_handler_returns_errors_list(self):
        result = _cw.lambda_handler({}, {})
        assert "errors" in result

    def test_forward_returns_false_when_no_dashboard_url(self):
        with patch.object(_cw, "SENTINEL_DASHBOARD_URL", ""):
            ok = _cw._forward_to_dashboard("svc", "P1", "title", {}, "iid-1")
        assert ok is False

    def test_forward_returns_false_when_requests_not_available(self):
        with patch.object(_cw, "SENTINEL_DASHBOARD_URL", "http://dash"), \
             patch.object(_cw, "_HAS_REQUESTS", False):
            ok = _cw._forward_to_dashboard("svc", "P1", "title", {}, "iid-1")
        assert ok is False


# ════════════════════════════════════════════════════════════════════════════
# Log analyzer
# ════════════════════════════════════════════════════════════════════════════

class TestLogAnalyzer:

    def test_classify_by_rules_fatal_is_p1(self):
        assert _la._classify_by_rules(["FATAL: system crash"]) == "P1"

    def test_classify_by_rules_critical_is_p1(self):
        assert _la._classify_by_rules(["CRITICAL alert fired"]) == "P1"

    def test_classify_by_rules_oom_is_p1(self):
        assert _la._classify_by_rules(["OOM killer invoked"]) == "P1"

    def test_classify_by_rules_error_is_p2(self):
        assert _la._classify_by_rules(["ERROR: connection refused"]) == "P2"

    def test_classify_by_rules_exception_is_p2(self):
        assert _la._classify_by_rules(["NullPointerException at line 42"]) == "P2"

    def test_classify_by_rules_timeout_is_p2(self):
        assert _la._classify_by_rules(["timeout waiting for DB"]) == "P2"

    def test_classify_by_rules_warn_is_p3(self):
        assert _la._classify_by_rules(["WARN: retry attempt 3"]) == "P3"

    def test_classify_by_rules_429_is_p3(self):
        assert _la._classify_by_rules(["HTTP 429 rate limited"]) == "P3"

    def test_classify_by_rules_info_is_p4(self):
        assert _la._classify_by_rules(["INFO: request completed"]) == "P4"

    def test_classify_by_rules_no_match_defaults_p4(self):
        assert _la._classify_by_rules(["everything is fine"]) == "P4"

    def test_classify_by_rules_empty_logs_is_p4(self):
        assert _la._classify_by_rules([]) == "P4"

    def test_merge_severity_p1_beats_p2(self):
        assert _la._merge_severity("P1", "P2") == "P1"

    def test_merge_severity_p2_beats_p3_when_ml_is_lower(self):
        assert _la._merge_severity("P3", "P2") == "P2"

    def test_merge_severity_equal_returns_same(self):
        assert _la._merge_severity("P2", "P2") == "P2"

    def test_merge_severity_p4_defers_to_p1(self):
        assert _la._merge_severity("P4", "P1") == "P1"

    def test_estimate_impact_error_rate(self):
        logs = ["ERROR: db down", "INFO: connected", "exception: timeout"]
        impact = _la._estimate_impact(logs, {})
        assert float(impact["error_rate_in_sample"]) == pytest.approx(2/3, abs=0.01)

    def test_estimate_impact_no_errors_is_zero(self):
        impact = _la._estimate_impact(["INFO: ok", "DEBUG: trace"], {})
        assert float(impact["error_rate_in_sample"]) == 0.0

    def test_estimate_impact_returns_decimal(self):
        impact = _la._estimate_impact(["ERROR"], {})
        assert isinstance(impact["error_rate_in_sample"], Decimal)

    def test_classify_by_model_falls_back_on_connection_error(self):
        with patch("requests.post", side_effect=Exception("connection refused")):
            result = _la._classify_by_model(["some log"], "svc")
        assert result["severity"] == "P3"
        assert result["degradation_trend"] == "stable"

    def test_classify_by_model_falls_back_on_bad_json_response(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"outputs": [{"data": ["not-json"]}]}
        with patch("requests.post", return_value=mock_resp):
            result = _la._classify_by_model(["log"], "svc")
        assert result["severity"] == "P3"

    def test_handler_processes_kinesis_records(self):
        batch  = {"incident_id": "inc-la-1", "logs": ["ERROR: db"], "service_name": "auth"}
        event  = {"Records": [_kinesis_record(batch)]}
        _mock_aws.reset_mock()
        _mock_aws.dynamodb.return_value.Table.return_value.update_item.return_value = {}
        _mock_aws.kinesis.return_value.put_record.return_value = {}
        resp_data = json.dumps({
            "severity": "P2", "degradation_trend": "worsening",
            "affected_components": ["auth"], "users_impacted": 20, "summary": "slow",
        })
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"outputs": [{"data": [resp_data]}]}
        with patch("requests.post", return_value=mock_resp):
            result = _la.handler(event, {})
        assert result["status"] == "ok"
        _mock_aws.dynamodb.return_value.Table.return_value.update_item.assert_called_once()
        _mock_aws.kinesis.return_value.put_record.assert_called_once()


# ════════════════════════════════════════════════════════════════════════════
# Loki bridge
# ════════════════════════════════════════════════════════════════════════════

class TestLokiBridge:

    def test_parse_body_plain_json(self):
        payload = {"alerts": [], "status": "firing"}
        result  = _lb._parse_body({"body": json.dumps(payload)})
        assert result["status"] == "firing"

    def test_parse_body_base64_encoded(self):
        payload = {"alerts": [], "status": "resolved"}
        encoded = base64.b64encode(json.dumps(payload).encode()).decode()
        result  = _lb._parse_body({"body": encoded, "isBase64Encoded": True})
        assert result["status"] == "resolved"

    def test_parse_body_base64_gzip(self):
        payload    = {"alerts": [{"status": "firing"}]}
        compressed = gzip.compress(json.dumps(payload).encode())
        encoded    = base64.b64encode(compressed).decode()
        result     = _lb._parse_body({"body": encoded, "isBase64Encoded": True})
        assert len(result["alerts"]) == 1

    def test_parse_body_empty_body(self):
        result = _lb._parse_body({"body": ""})
        assert result == {}

    def test_severity_to_error_rate_critical(self):
        assert _lb._severity_to_error_rate("critical", "firing") == 0.20

    def test_severity_to_error_rate_error(self):
        assert _lb._severity_to_error_rate("error", "firing") == 0.15

    def test_severity_to_error_rate_warning(self):
        assert _lb._severity_to_error_rate("warning", "firing") == 0.10

    def test_severity_to_error_rate_info(self):
        assert _lb._severity_to_error_rate("info", "firing") == 0.02

    def test_severity_to_error_rate_resolved_always_zero(self):
        assert _lb._severity_to_error_rate("critical", "resolved") == 0.0

    def test_severity_to_error_rate_unknown_defaults_to_warning(self):
        assert _lb._severity_to_error_rate("unknown", "firing") == 0.10

    def test_handler_returns_400_on_bad_body(self):
        result = _lb.handler({"body": "not-json"}, {})
        assert result["statusCode"] == 400

    def test_handler_forwards_firing_alert(self):
        alert = {
            "status": "firing",
            "labels": {"service": "auth", "severity": "critical"},
            "annotations": {"summary": "DB down"},
            "startsAt": "2026-05-20T10:00:00Z",
        }
        event = {"body": json.dumps({"alerts": [alert], "status": "firing"})}
        with patch("boto3.client") as mock_cli:
            mock_cli.return_value.put_record.return_value = {}
            result = _lb.handler(event, {})
        assert result["statusCode"] == 200
        assert json.loads(result["body"])["forwarded"] == 1
        mock_cli.return_value.put_record.assert_called_once()

    def test_handler_counts_zero_when_kinesis_fails(self):
        alert = {"status": "firing", "labels": {}, "annotations": {}}
        event = {"body": json.dumps({"alerts": [alert]})}
        with patch("boto3.client") as mock_cli:
            mock_cli.return_value.put_record.side_effect = Exception("Kinesis unreachable")
            result = _lb.handler(event, {})
        assert result["statusCode"] == 200
        assert json.loads(result["body"])["forwarded"] == 0

    def test_handler_empty_alerts_list(self):
        event = {"body": json.dumps({"alerts": [], "status": "firing"})}
        with patch("boto3.client"):
            result = _lb.handler(event, {})
        assert result["statusCode"] == 200
        assert json.loads(result["body"])["forwarded"] == 0


# ════════════════════════════════════════════════════════════════════════════
# Validator
# ════════════════════════════════════════════════════════════════════════════

class TestValidator:

    def test_check_health_endpoint_healthy(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("requests.get", return_value=mock_resp):
            assert _val._check_health_endpoint("http://svc/health") is True

    def test_check_health_endpoint_returns_false_on_500(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("requests.get", return_value=mock_resp):
            assert _val._check_health_endpoint("http://svc/health") is False

    def test_check_health_endpoint_returns_false_on_exception(self):
        with patch("requests.get", side_effect=Exception("timeout")):
            assert _val._check_health_endpoint("http://svc/health") is False

    def test_check_health_endpoint_404_is_healthy(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        with patch("requests.get", return_value=mock_resp):
            assert _val._check_health_endpoint("http://svc/health") is True

    def test_check_metric_consensus_healthy_returns_true(self):
        assert _val._check_metric_consensus({"error_rate": 0.01, "p99_latency_ms": 100}) is True

    def test_check_metric_consensus_high_error_rate_returns_false(self):
        assert _val._check_metric_consensus({"error_rate": 0.10, "p99_latency_ms": 100}) is False

    def test_check_metric_consensus_high_latency_returns_false(self):
        assert _val._check_metric_consensus({"error_rate": 0.01, "p99_latency_ms": 3000}) is False

    def test_check_metric_consensus_zero_values_returns_true(self):
        assert _val._check_metric_consensus({}) is True

    def test_to_dynamodb_safe_converts_floats(self):
        result = _val._to_dynamodb_safe({"rate": 0.05, "nested": {"val": 1.5}})
        assert isinstance(result["rate"], Decimal)
        assert isinstance(result["nested"]["val"], Decimal)

    def test_to_dynamodb_safe_preserves_non_floats(self):
        result = _val._to_dynamodb_safe({"name": "auth", "count": 5, "flag": True})
        assert result["name"] == "auth"
        assert result["count"] == 5
        assert result["flag"] is True

    def test_to_dynamodb_safe_handles_lists(self):
        result = _val._to_dynamodb_safe({"items": [0.1, 0.2]})
        assert all(isinstance(v, Decimal) for v in result["items"])

    def test_validate_alert_is_real_when_two_signals_fail(self):
        alert = {"alert_id": "a1", "service_name": "auth",
                 "error_rate": 0.15, "p99_latency_ms": 3000, "health_endpoint": ""}
        _mock_aws.reset_mock()
        _mock_aws.dynamodb.return_value.Table.return_value.put_item.return_value = {}
        _mock_aws.sqs.return_value.send_message.return_value = {}
        result = _val._validate_alert(alert)
        assert result["is_real_incident"] == "true"

    def test_validate_alert_is_false_positive_when_signals_pass(self):
        alert = {"alert_id": "a2", "service_name": "svc",
                 "error_rate": 0.01, "p99_latency_ms": 50, "health_endpoint": ""}
        _mock_aws.reset_mock()
        _mock_aws.dynamodb.return_value.Table.return_value.put_item.return_value = {}
        _mock_aws.sqs.return_value.send_message.return_value = {}
        result = _val._validate_alert(alert)
        assert result["is_real_incident"] == "false"

    def test_handler_processes_kinesis_record(self):
        alert = {"alert_id": "a3", "service_name": "svc",
                 "error_rate": 0.01, "p99_latency_ms": 50}
        event = {"Records": [_kinesis_record(alert)]}
        _mock_aws.reset_mock()
        _mock_aws.dynamodb.return_value.Table.return_value.put_item.return_value = {}
        _mock_aws.sqs.return_value.send_message.return_value = {}
        result = _val.handler(event, {})
        assert result["processed"] == 1


# ════════════════════════════════════════════════════════════════════════════
# Root cause agent
# ════════════════════════════════════════════════════════════════════════════

def _rca_incident(iid="i1"):
    return {"Item": {
        "incident_id": iid, "severity": "P1", "service_name": "auth",
        "impact_scope": {"summary": "high error rate"}, "log_analysis": {},
    }}


class TestRootCauseAgent:

    def test_handler_unknown_action_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown action"):
            _rca.handler({"action": "explode", "incident_id": "i1", "state": {}}, {})

    def test_handler_merges_state_and_tags_action(self):
        _mock_aws.reset_mock()
        _mock_aws.dynamodb.return_value.Table.return_value.get_item.return_value = _rca_incident()
        commits_resp = MagicMock()
        commits_resp.ok   = True
        commits_resp.json.return_value = [
            {"sha": "abc123def456",
             "commit": {"message": "fix auth flow", "author": {"name": "dev"}}}
        ]
        with patch("requests.get", return_value=commits_resp):
            result = _rca.handler({
                "action": "fetch_recent_changes",
                "incident_id": "i1",
                "state": {"extra_key": "preserved"},
            }, {})
        assert result["action_completed"] == "fetch_recent_changes"
        assert result["incident_id"] == "i1"
        assert result["extra_key"] == "preserved"
        assert "recent_commits" in result

    def test_fetch_recent_changes_returns_commit_list(self):
        _mock_aws.reset_mock()
        _mock_aws.dynamodb.return_value.Table.return_value.get_item.return_value = _rca_incident()
        commits_resp = MagicMock()
        commits_resp.ok   = True
        commits_resp.json.return_value = [
            {"sha": "deadbeef1234",
             "commit": {"message": "deploy v2", "author": {"name": "alice"}}}
        ] * 3
        with patch("requests.get", return_value=commits_resp):
            result = _rca._fetch_recent_changes("i1", {})
        assert len(result["recent_commits"]) == 3
        assert result["recent_commits"][0]["sha"] == "deadbeef"

    def test_fetch_recent_changes_returns_empty_on_github_error(self):
        commits_resp = MagicMock()
        commits_resp.ok   = False
        commits_resp.json.return_value = []
        with patch("requests.get", return_value=commits_resp):
            result = _rca._fetch_recent_changes("i1", {})
        assert result["recent_commits"] == []

    def test_query_rag_returns_similar_incidents(self):
        _mock_aws.reset_mock()
        _mock_aws.dynamodb.return_value.Table.return_value.get_item.return_value = _rca_incident()
        rag_resp = MagicMock()
        rag_resp.ok   = True
        rag_resp.json.return_value = {"results": [{"id": "past-1", "score": 0.9}]}
        with patch("requests.post", return_value=rag_resp):
            result = _rca._query_rag("i1", {})
        assert len(result["similar_past_incidents"]) == 1

    def test_query_rag_returns_empty_on_failure(self):
        _mock_aws.reset_mock()
        _mock_aws.dynamodb.return_value.Table.return_value.get_item.return_value = _rca_incident()
        rag_resp = MagicMock()
        rag_resp.ok = False
        with patch("requests.post", return_value=rag_resp):
            result = _rca._query_rag("i1", {})
        assert result["similar_past_incidents"] == []

    def test_analyze_root_cause_falls_back_on_invalid_json(self):
        _mock_aws.reset_mock()
        _mock_aws.dynamodb.return_value.Table.return_value.get_item.return_value = _rca_incident("i2")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"outputs": [{"data": ["not valid json"]}]}
        with patch("requests.post", return_value=mock_resp):
            result = _rca._analyze_root_cause(
                "i2", {"recent_commits": [], "similar_past_incidents": []}
            )
        assert "root_cause_analysis" in result
        assert result["root_cause_analysis"]["confidence"] == "low"

    def test_generate_runbook_falls_back_on_invalid_json(self):
        _mock_aws.reset_mock()
        _mock_aws.dynamodb.return_value.Table.return_value.get_item.return_value = {
            "Item": {"incident_id": "i3", "severity": "P1"}
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"outputs": [{"data": ["plain text, not json"]}]}
        with patch("requests.post", return_value=mock_resp):
            result = _rca._generate_runbook(
                "i3", {"root_cause_analysis": {"root_cause": "oom", "missed_edge_case": ""}}
            )
        assert "runbook" in result
        assert isinstance(result["runbook"]["immediate_actions"], list)

    def test_publish_report_updates_dynamodb_and_kinesis(self):
        _mock_aws.reset_mock()
        _mock_aws.dynamodb.return_value.Table.return_value.get_item.return_value = {
            "Item": {"incident_id": "i4", "severity": "P1"}
        }
        _mock_aws.dynamodb.return_value.Table.return_value.update_item.return_value = {}
        _mock_aws.kinesis.return_value.put_record.return_value = {}
        result = _rca._publish_report("i4", {
            "root_cause_analysis": {"root_cause": "oom"},
            "runbook": {},
            "recent_commits": [],
            "similar_past_incidents": [],
        })
        assert "report_id" in result
        _mock_aws.dynamodb.return_value.Table.return_value.update_item.assert_called_once()
        _mock_aws.kinesis.return_value.put_record.assert_called_once()


# ════════════════════════════════════════════════════════════════════════════
# PR security agent
# ════════════════════════════════════════════════════════════════════════════

class TestPRSecurityAgent:

    def test_verify_signature_valid(self):
        body = '{"action":"opened"}'
        assert _pr._verify_signature(body, {"X-Hub-Signature-256": _make_sig(body)}) is True

    def test_verify_signature_invalid_digest(self):
        assert _pr._verify_signature(
            '{"action":"opened"}', {"X-Hub-Signature-256": "sha256=bad"}
        ) is False

    def test_verify_signature_missing_header(self):
        assert _pr._verify_signature('{}', {}) is False

    def test_verify_signature_wrong_secret(self):
        body = '{"action":"opened"}'
        sig  = _make_sig(body, secret="wrong-secret")
        assert _pr._verify_signature(body, {"X-Hub-Signature-256": sig}) is False

    def test_handler_invalid_signature_returns_401(self):
        event  = {"body": '{}', "headers": {"X-Hub-Signature-256": "sha256=invalid"}}
        result = _pr.handler(event, {})
        assert result["statusCode"] == 401

    def test_handler_skips_closed_action(self):
        body   = json.dumps({"action": "closed"})
        event  = {"body": body, "headers": {"X-Hub-Signature-256": _make_sig(body)}}
        result = _pr.handler(event, {})
        assert result["statusCode"] == 200
        assert result["body"] == "Skipped"

    def test_handler_skips_labeled_action(self):
        body   = json.dumps({"action": "labeled"})
        event  = {"body": body, "headers": {"X-Hub-Signature-256": _make_sig(body)}}
        result = _pr.handler(event, {})
        assert result["statusCode"] == 200
        assert result["body"] == "Skipped"

    def test_run_security_analysis_falls_back_on_bad_json(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"outputs": [{"data": ["plain text"]}]}
        mock_resp.raise_for_status.return_value = None
        with patch("requests.post", return_value=mock_resp):
            result = _pr._run_security_analysis(
                "diff content", {"title": "Fix auth", "user": {"login": "dev"}}
            )
        assert result["severity"] == "unknown"
        assert "review_id" in result
        assert "analyzed_at" in result

    def test_run_security_analysis_parses_valid_json(self):
        analysis = {
            "severity": "medium",
            "issues": ["SQL injection on line 5"],
            "edge_cases_missed": [],
            "recommendations": ["Use parameterized queries"],
            "summary": "One issue found.",
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"outputs": [{"data": [json.dumps(analysis)]}]}
        mock_resp.raise_for_status.return_value = None
        with patch("requests.post", return_value=mock_resp):
            result = _pr._run_security_analysis(
                "diff content", {"title": "Add auth", "user": {"login": "dev"}}
            )
        assert result["severity"] == "medium"
        assert len(result["issues"]) == 1
        assert "review_id" in result

    def test_store_result_calls_put_item(self):
        _mock_aws.reset_mock()
        _mock_aws.dynamodb.return_value.Table.return_value.put_item.return_value = {}
        pr_data  = {"number": 42, "title": "Add feature", "user": {"login": "dev"}}
        analysis = {
            "review_id":   "r1",
            "analyzed_at": "2026-05-26T10:00:00+00:00",
            "severity":    "low",
            "issues":      [],
            "edge_cases_missed": [],
        }
        _pr._store_result(pr_data, analysis)
        _mock_aws.dynamodb.return_value.Table.return_value.put_item.assert_called_once()
        call_kwargs = _mock_aws.dynamodb.return_value.Table.return_value.put_item.call_args
        item = call_kwargs.kwargs.get("Item") or call_kwargs.args[0].get("Item")
        assert item["pr_id"] == "42"
        assert item["risk_level"] == "low"

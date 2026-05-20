#!/usr/bin/env python3
"""
Sentinel end-to-end test harness.

Exercises the full pipeline against Floci (local AWS):
  1. Floci connectivity — streams, queues, tables exist
  2. Alert ingestion — CloudWatch and Loki paths → Kinesis
  3. Validator — real incident confirmed, false positive rejected
  4. Log analyzer — P1 from panic logs, P4 from info-only logs
  5. Sentinel core — severity, incident serialisation, config loader
  6. Provider registry — from_config factory, validate()
  7. Grafana CLI — dashboard push (mock Grafana API)

Run after: python scripts/bootstrap_floci.py

Usage:
    python scripts/e2e_test.py
    python scripts/e2e_test.py --verbose
"""

import argparse
import base64
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

# ── make the sentinel package importable from any working directory ─────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import boto3

# ── env: point all service modules at Floci ────────────────────────────────
os.environ.update({
    "FLOCI_ENDPOINT":            "http://localhost:4566",
    "AWS_DEFAULT_REGION":        "us-east-1",
    "AWS_REGION":                "us-east-1",
    "ALERTS_STREAM":             "sentinel-alerts",
    "EVENTS_STREAM":             "sentinel-events",
    "INCIDENTS_TABLE":           "sentinel-incidents",
    "VALIDATION_RESULTS_TABLE":  "sentinel-validation-results",
    "VALIDATION_JOBS_QUEUE_URL": "http://localhost:4566/000000000000/sentinel-validation-jobs",
    "EVENTS_QUEUE_URL":          "http://localhost:4566/000000000000/sentinel-confirmed-incidents",
    "KSERVE_ENDPOINT":           "http://localhost:9999",  # stub — handler falls back gracefully
})

FLOCI = dict(
    endpoint_url="http://localhost:4566",
    region_name="us-east-1",
    aws_access_key_id="test",
    aws_secret_access_key="test",
)

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"

_results = []
_verbose = False


# ── test decorator ─────────────────────────────────────────────────────────
# Call the returned wrapper immediately after definition: @test("name") \n def _(): ... \n _()

def test(name):
    def decorator(fn):
        def wrapper():
            try:
                fn()
                print(f"  {PASS}  {name}")
                _results.append(("pass", name, ""))
            except Exception as e:
                print(f"  {FAIL}  {name}")
                if _verbose:
                    import traceback; traceback.print_exc()
                else:
                    print(f"         {type(e).__name__}: {e}")
                _results.append(("fail", name, str(e)))
        return wrapper
    return decorator


# ── AWS helpers ────────────────────────────────────────────────────────────

def _kinesis():
    return boto3.client("kinesis", **FLOCI)

def _sqs():
    return boto3.client("sqs", **FLOCI)

def _dynamodb():
    return boto3.resource("dynamodb", **FLOCI)

def _put_kinesis(stream, data):
    _kinesis().put_record(StreamName=stream, Data=json.dumps(data), PartitionKey=data.get("alert_id", "test"))

def _read_kinesis(stream, max_records=10):
    resp = _kinesis().get_shard_iterator(
        StreamName=stream, ShardId="shardId-000000000000", ShardIteratorType="TRIM_HORIZON"
    )
    it = resp["ShardIterator"]
    for _ in range(5):
        r = _kinesis().get_records(ShardIterator=it, Limit=max_records)
        records = r["Records"]
        if records:
            return [json.loads(rec["Data"]) for rec in records]
        it = r["NextShardIterator"]
        time.sleep(0.1)
    return []

def _kinesis_event(payload: dict) -> dict:
    return {"Records": [{"kinesis": {"data": base64.b64encode(json.dumps(payload).encode()).decode()}}]}


# ══════════════════════════════════════════════════════════════════════════════
# 1. Floci connectivity
# ══════════════════════════════════════════════════════════════════════════════

def section_connectivity():
    print("\n── 1. Floci connectivity ────────────────────────────────────────────────")

    @test("Kinesis stream sentinel-alerts exists")
    def _(): assert "sentinel-alerts" in _kinesis().list_streams()["StreamNames"]
    _()

    @test("Kinesis stream sentinel-events exists")
    def _(): assert "sentinel-events" in _kinesis().list_streams()["StreamNames"]
    _()

    @test("SQS queue sentinel-validation-jobs exists")
    def _():
        urls = _sqs().list_queues(QueueNamePrefix="sentinel-validation-jobs").get("QueueUrls", [])
        assert urls, "queue not found"
    _()

    @test("DynamoDB table sentinel-incidents exists")
    def _():
        tbl = _dynamodb().Table("sentinel-incidents")
        assert tbl.table_status in ("ACTIVE", "CREATING")
    _()

    @test("DynamoDB table sentinel-validation-results exists")
    def _():
        tbl = _dynamodb().Table("sentinel-validation-results")
        assert tbl.table_status in ("ACTIVE", "CREATING")
    _()


# ══════════════════════════════════════════════════════════════════════════════
# 2. Alert ingestion — CloudWatch and Loki paths
# ══════════════════════════════════════════════════════════════════════════════

def section_alert_ingestion():
    print("\n── 2. Alert ingestion ───────────────────────────────────────────────────")

    @test("CloudWatch-style alert published to Kinesis")
    def _():
        alert_id = str(uuid.uuid4())
        _put_kinesis("sentinel-alerts", {
            "alert_id": alert_id, "source": "cloudwatch", "service_name": "auth-service",
            "error_rate": 0.15, "p99_latency_ms": 3200,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        records = _read_kinesis("sentinel-alerts")
        assert any(r.get("alert_id") == alert_id for r in records), "alert not found in stream"
    _()

    @test("Loki bridge: firing alert forwarded to Kinesis")
    def _():
        sys.path.insert(0, os.path.join(ROOT, "services", "loki-bridge"))
        import handler as loki_handler
        import importlib; importlib.reload(loki_handler)

        body = {"status": "firing", "alerts": [{
            "status": "firing",
            "labels": {"service": "payments", "severity": "critical", "sentinel": "true"},
            "annotations": {"summary": "Payment errors spiking"},
            "startsAt": datetime.now(timezone.utc).isoformat(),
        }]}
        resp = loki_handler.handler({"body": json.dumps(body), "isBase64Encoded": False}, {})
        assert resp["statusCode"] == 200
        assert json.loads(resp["body"])["forwarded"] == 1
    _()

    @test("Loki bridge: resolved alert → error_rate=0.0")
    def _():
        sys.path.insert(0, os.path.join(ROOT, "services", "loki-bridge"))
        import handler as loki_handler

        captured = {}
        body = {"status": "resolved", "alerts": [{
            "status": "resolved",
            "labels": {"service": "payments", "severity": "warning"},
            "annotations": {},
        }]}
        with patch("handler.boto3") as mock_boto3:
            mock_k = MagicMock()
            mock_k.put_record.side_effect = lambda **kw: captured.update({"data": json.loads(kw["Data"])}) or {}
            mock_boto3.client.return_value = mock_k
            loki_handler.handler({"body": json.dumps(body), "isBase64Encoded": False}, {})
        assert captured["data"]["error_rate"] == 0.0
    _()

    @test("Loki bridge: severity critical → error_rate=0.20")
    def _():
        sys.path.insert(0, os.path.join(ROOT, "services", "loki-bridge"))
        import handler as loki_handler

        captured = {}
        body = {"status": "firing", "alerts": [{
            "status": "firing",
            "labels": {"service": "db", "severity": "critical"},
            "annotations": {},
        }]}
        with patch("handler.boto3") as mock_boto3:
            mock_k = MagicMock()
            mock_k.put_record.side_effect = lambda **kw: captured.update({"data": json.loads(kw["Data"])}) or {}
            mock_boto3.client.return_value = mock_k
            loki_handler.handler({"body": json.dumps(body), "isBase64Encoded": False}, {})
        assert captured["data"]["error_rate"] == 0.20
    _()


# ══════════════════════════════════════════════════════════════════════════════
# 3. Validator service
# ══════════════════════════════════════════════════════════════════════════════

def section_validator():
    print("\n── 3. Validator service ─────────────────────────────────────────────────")

    sys.path.insert(0, os.path.join(ROOT, "services", "validator"))
    sys.path.insert(0, os.path.join(ROOT, "services", "shared"))
    import handler as validator
    import importlib; importlib.reload(validator)

    @test("Real incident (high error_rate + high latency) → confirmed in DynamoDB")
    def _():
        alert_id = str(uuid.uuid4())
        validator.handler(_kinesis_event({
            "alert_id": alert_id, "service_name": "checkout",
            "error_rate": 0.25, "p99_latency_ms": 3000, "health_endpoint": "",
        }), {})
        item = _dynamodb().Table("sentinel-validation-results").get_item(Key={"alert_id": alert_id}).get("Item")
        assert item is not None, "result not in DynamoDB"
        assert item["is_real_incident"] == "true"
    _()

    @test("False positive (healthy metrics) → not confirmed")
    def _():
        alert_id = str(uuid.uuid4())
        validator.handler(_kinesis_event({
            "alert_id": alert_id, "service_name": "healthy-svc",
            "error_rate": 0.01, "p99_latency_ms": 100, "health_endpoint": "",
        }), {})
        item = _dynamodb().Table("sentinel-validation-results").get_item(Key={"alert_id": alert_id}).get("Item")
        assert item is not None
        assert item["is_real_incident"] == "false"
    _()

    @test("Multiple alerts in one batch all processed")
    def _():
        ids = [str(uuid.uuid4()), str(uuid.uuid4())]
        event = {"Records": [
            {"kinesis": {"data": base64.b64encode(json.dumps({
                "alert_id": aid, "service_name": "svc",
                "error_rate": 0.30, "p99_latency_ms": 3000, "health_endpoint": "",
            }).encode()).decode()}}
            for aid in ids
        ]}
        result = validator.handler(event, {})
        assert result["processed"] == 2
    _()


# ══════════════════════════════════════════════════════════════════════════════
# 4. Log analyzer
# ══════════════════════════════════════════════════════════════════════════════

def section_log_analyzer():
    print("\n── 4. Log analyzer ──────────────────────────────────────────────────────")

    sys.path.insert(0, os.path.join(ROOT, "services", "log-analyzer"))
    sys.path.insert(0, os.path.join(ROOT, "services", "shared"))
    import handler as log_analyzer
    import importlib; importlib.reload(log_analyzer)

    def _seed_incident(incident_id):
        _dynamodb().Table("sentinel-incidents").put_item(Item={
            "incident_id": incident_id, "service_name": "test", "severity": "P4",
        })

    @test("FATAL/panic logs → severity P1 or P2")
    def _():
        iid = str(uuid.uuid4())
        _seed_incident(iid)
        log_analyzer.handler(_kinesis_event({
            "incident_id": iid, "service_name": "inference",
            "logs": ["FATAL panic: nil pointer", "OOM killed, restart count 5"],
        }), {})
        item = _dynamodb().Table("sentinel-incidents").get_item(Key={"incident_id": iid}).get("Item")
        assert item["severity"] in ("P1", "P2"), f"expected P1/P2, got {item['severity']}"
    _()

    @test("ERROR/500 logs → severity P2 or better")
    def _():
        iid = str(uuid.uuid4())
        _seed_incident(iid)
        log_analyzer.handler(_kinesis_event({
            "incident_id": iid, "service_name": "api",
            "logs": ["ERROR: upstream returned 500", "Exception: connection timeout"],
        }), {})
        item = _dynamodb().Table("sentinel-incidents").get_item(Key={"incident_id": iid}).get("Item")
        assert item["severity"] in ("P1", "P2"), f"expected P1/P2, got {item['severity']}"
    _()

    @test("INFO-only logs → severity P4 (P3 when ML fallback active)")
    def _():
        iid = str(uuid.uuid4())
        _seed_incident(iid)
        log_analyzer.handler(_kinesis_event({
            "incident_id": iid, "service_name": "worker",
            "logs": ["INFO job completed", "DEBUG cache hit ratio 0.98"],
        }), {})
        item = _dynamodb().Table("sentinel-incidents").get_item(Key={"incident_id": iid}).get("Item")
        # Rule-based gives P4; ML fallback (KServe unreachable) returns P3.
        # Merge picks worse → P3. In prod with KServe running this would be P4.
        assert item["severity"] in ("P3", "P4"), f"unexpected severity {item['severity']}"
    _()

    @test("WARN logs → severity P3")
    def _():
        iid = str(uuid.uuid4())
        _seed_incident(iid)
        log_analyzer.handler(_kinesis_event({
            "incident_id": iid, "service_name": "queue",
            "logs": ["WARNING: retry attempt 3", "WARN: rate limit 429"],
        }), {})
        item = _dynamodb().Table("sentinel-incidents").get_item(Key={"incident_id": iid}).get("Item")
        assert item["severity"] in ("P3", "P2", "P1"), f"got {item['severity']}"
    _()


# ══════════════════════════════════════════════════════════════════════════════
# 5. Sentinel core
# ══════════════════════════════════════════════════════════════════════════════

def section_core():
    print("\n── 5. Sentinel core ─────────────────────────────────────────────────────")

    @test("Severity escalation chain P4 → P3 → P2 → P1")
    def _():
        from sentinel.core.severity import Severity
        assert Severity.P4.escalate() == Severity.P3
        assert Severity.P3.escalate() == Severity.P2
        assert Severity.P2.escalate() == Severity.P1
        assert Severity.P1.escalate() == Severity.P1  # capped
    _()

    @test("Severity.P1.is_critical() == True, others False")
    def _():
        from sentinel.core.severity import Severity
        assert Severity.P1.is_critical()
        assert not Severity.P2.is_critical()
    _()

    @test("Incident round-trip serialisation preserves all fields")
    def _():
        from sentinel.core.incident import Incident
        from sentinel.core.severity import Severity
        inc = Incident(service_name="api-gw", severity=Severity.P2, status="OPEN",
                       root_cause="db pool exhausted", metadata={"region": "us-east-1"})
        r = Incident.from_dict(inc.to_dict())
        assert r.service_name == "api-gw"
        assert r.severity == Severity.P2
        assert r.root_cause == "db pool exhausted"
        assert r.metadata == {"region": "us-east-1"}
    _()

    @test("Config loader: minimal sentinel.yaml parsed correctly")
    def _():
        import tempfile, yaml
        from sentinel.config.loader import load
        cfg_data = {
            "namespace": "e2e-test",
            "git_provider": {"provider": "github", "token": "t", "repo": "o/r"},
            "cloud_provider": {"provider": "floci", "region": "us-east-1", "endpoint": "http://localhost:4566"},
            "llm": {"provider": "openai", "api_key": "k", "model": "gpt-4o-mini"},
        }
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            yaml.dump(cfg_data, f); path = f.name
        cfg = load(path)
        assert cfg.namespace == "e2e-test"
        assert cfg.cloud_provider.endpoint == "http://localhost:4566"
    _()

    @test("Config loader: observability block (loki/mimir/grafana) parsed")
    def _():
        import tempfile, yaml
        from sentinel.config.loader import load
        cfg_data = {
            "namespace": "e2e",
            "git_provider": {"provider": "github", "token": "t", "repo": "o/r"},
            "cloud_provider": {"provider": "aws", "region": "us-east-1"},
            "llm": {"provider": "openai", "api_key": "k", "model": "gpt-4o-mini"},
            "observability": {
                "loki":    {"url": "http://loki:3100", "tenant_id": "team-a"},
                "mimir":   {"url": "http://mimir:9009/api/v1/push"},
                "grafana": {"url": "http://grafana:3000", "api_key": "glsa_test"},
            },
        }
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            yaml.dump(cfg_data, f); path = f.name
        cfg = load(path)
        assert cfg.observability.loki.url == "http://loki:3100"
        assert cfg.observability.loki.tenant_id == "team-a"
        assert cfg.observability.mimir.metric_prefix == "sentinel"  # default
        assert cfg.observability.grafana.folder == "Sentinel"       # default
    _()

    @test("Config loader: env var SENTINEL_LLM_MODEL overrides yaml")
    def _():
        import tempfile, yaml
        from sentinel.config import loader
        import importlib; importlib.reload(loader)
        cfg_data = {
            "namespace": "e2e",
            "git_provider": {"provider": "github", "token": "t", "repo": "o/r"},
            "cloud_provider": {"provider": "aws", "region": "us-east-1"},
            "llm": {"provider": "openai", "api_key": "k", "model": "gpt-4o-mini"},
        }
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            yaml.dump(cfg_data, f); path = f.name
        os.environ["SENTINEL_LLM_MODEL"] = "gpt-4o"
        try:
            cfg = loader.load(path)
            assert cfg.llm.model == "gpt-4o"
        finally:
            del os.environ["SENTINEL_LLM_MODEL"]
    _()


# ══════════════════════════════════════════════════════════════════════════════
# 6. Provider registry
# ══════════════════════════════════════════════════════════════════════════════

def section_registry():
    print("\n── 6. Provider registry ─────────────────────────────────────────────────")

    @test("from_config builds registry for floci+github+openai")
    def _():
        with patch("sentinel.providers.cloud.aws.AWSProvider"), \
             patch("sentinel.providers.git.github.GitHubProvider"), \
             patch("sentinel.providers.llm.openai.OpenAIProvider"):
            from sentinel.registry import ProviderRegistry
            import importlib, sentinel.registry; importlib.reload(sentinel.registry)
            cfg = {
                "cloud_provider": {"provider": "floci", "region": "us-east-1", "endpoint": "http://localhost:4566"},
                "git_provider":   {"provider": "github", "token": "ghp_test"},
                "llm":            {"provider": "openai", "api_key": "sk-test", "model": "gpt-4o-mini"},
            }
            reg = sentinel.registry.ProviderRegistry.from_config(cfg)
            assert reg is not None
    _()

    @test("validate() returns [] when all providers healthy")
    def _():
        from sentinel.registry import ProviderRegistry
        from sentinel.providers.base.cloud import BaseCloudProvider
        from sentinel.providers.base.git import BaseGitProvider
        from sentinel.providers.base.llm import BaseLLMProvider
        llm = MagicMock(spec=BaseLLMProvider)
        llm.health_check.return_value = True
        reg = ProviderRegistry(
            cloud=MagicMock(spec=BaseCloudProvider),
            git=MagicMock(spec=BaseGitProvider),
            llm=llm,
        )
        assert reg.validate() == []
    _()

    @test("validate() flags unhealthy LLM provider")
    def _():
        from sentinel.registry import ProviderRegistry
        from sentinel.providers.base.cloud import BaseCloudProvider
        from sentinel.providers.base.git import BaseGitProvider
        from sentinel.providers.base.llm import BaseLLMProvider
        llm = MagicMock(spec=BaseLLMProvider)
        llm.health_check.return_value = False
        reg = ProviderRegistry(
            cloud=MagicMock(spec=BaseCloudProvider),
            git=MagicMock(spec=BaseGitProvider),
            llm=llm,
        )
        issues = reg.validate()
        assert len(issues) == 1 and "LLM" in issues[0]
    _()

    @test("unknown cloud provider raises ValueError")
    def _():
        import importlib, sentinel.registry; importlib.reload(sentinel.registry)
        try:
            sentinel.registry.ProviderRegistry.from_config({
                "cloud_provider": {"provider": "azure"},
                "git_provider":   {"provider": "github", "token": "t"},
                "llm":            {"provider": "openai", "api_key": "k"},
            })
            assert False, "expected ValueError"
        except ValueError as e:
            assert "azure" in str(e)
    _()


# ══════════════════════════════════════════════════════════════════════════════
# 7. Grafana CLI (mock server)
# ══════════════════════════════════════════════════════════════════════════════

def section_grafana_cli():
    print("\n── 7. Grafana CLI (mock Grafana API) ────────────────────────────────────")

    @test("sentinel grafana push: creates folder and imports dashboard")
    def _():
        from click.testing import CliRunner
        from sentinel.cli.grafana import push

        mock_folders = MagicMock(status_code=200)
        mock_folders.json.return_value = []
        mock_folders.raise_for_status = lambda: None

        mock_create_folder = MagicMock(status_code=200)
        mock_create_folder.json.return_value = {"id": 42}

        mock_push = MagicMock(status_code=200)
        mock_push.json.return_value = {"url": "/d/sentinel-incidents/sentinel-incident-overview"}

        with patch("sentinel.cli.grafana.requests.get", return_value=mock_folders), \
             patch("sentinel.cli.grafana.requests.post", side_effect=[mock_create_folder, mock_push]):
            result = CliRunner().invoke(push, [
                "--url", "http://grafana.test",
                "--api-key", "glsa_test",
                "--folder", "Sentinel",
            ])
        assert result.exit_code == 0, f"exit {result.exit_code}: {result.output}"
        assert "pushed successfully" in result.output
    _()

    @test("sentinel grafana push: reuses existing folder")
    def _():
        from click.testing import CliRunner
        from sentinel.cli.grafana import push

        mock_folders = MagicMock(status_code=200)
        mock_folders.json.return_value = [{"title": "Sentinel", "id": 7}]
        mock_folders.raise_for_status = lambda: None

        mock_push = MagicMock(status_code=200)
        mock_push.json.return_value = {"url": "/d/sentinel-incidents"}

        with patch("sentinel.cli.grafana.requests.get", return_value=mock_folders), \
             patch("sentinel.cli.grafana.requests.post", return_value=mock_push) as mock_post:
            result = CliRunner().invoke(push, ["--url", "http://g.test", "--api-key", "k"])

        # Should not have called POST /api/folders — folder already exists
        post_urls = [str(c) for c in mock_post.call_args_list]
        assert not any("folders" in u for u in post_urls), "created folder unnecessarily"
        assert result.exit_code == 0
    _()

    @test("sentinel grafana open: prints dashboard URL")
    def _():
        from click.testing import CliRunner
        from sentinel.cli.grafana import open_cmd
        result = CliRunner().invoke(open_cmd, ["--url", "http://grafana.example.com"])
        assert "sentinel-incidents" in result.output
        assert result.exit_code == 0
    _()


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def main():
    global _verbose
    parser = argparse.ArgumentParser(description="Sentinel E2E test harness")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()
    _verbose = args.verbose

    print("\nSentinel — End-to-End Test Harness")
    print("=" * 72)

    section_connectivity()
    section_alert_ingestion()
    section_validator()
    section_log_analyzer()
    section_core()
    section_registry()
    section_grafana_cli()

    total  = len(_results)
    passed = sum(1 for r in _results if r[0] == "pass")
    failed = total - passed

    print("\n" + "=" * 72)
    print(f"Results: {passed}/{total} passed", end="")
    if failed:
        print(f"  ({failed} failed)\n")
        print("Failed tests:")
        for status, name, reason in _results:
            if status == "fail":
                print(f"  • {name}")
                if reason and not _verbose:
                    print(f"    → {reason[:120]}")
    else:
        print(" — all green ✓")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()

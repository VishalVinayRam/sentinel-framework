#!/usr/bin/env python3
"""
Local Sentinel pipeline runner.

Simulates what AWS Lambda + Kinesis triggers do in production:
  sentinel-alerts  stream → validator handler → confirmed incidents
  sentinel-logs    stream → log-analyzer handler → severity + RCA
  sentinel-confirmed-incidents SQS → creates DynamoDB record + calls root-cause-agent

Run alongside setup_demo.sh to see the full pipeline end-to-end.

Usage:  python3 sentinel_pipeline.py
"""
import base64
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import boto3
import requests

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# ── AWS / Floci ───────────────────────────────────────────────────────────────
FLOCI = dict(
    endpoint_url="http://localhost:4566",
    region_name="us-east-1",
    aws_access_key_id="test",
    aws_secret_access_key="test",
)
os.environ.setdefault("FLOCI_ENDPOINT",            "http://localhost:4566")
os.environ.setdefault("AWS_DEFAULT_REGION",         "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID",          "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY",      "test")
os.environ.setdefault("INCIDENTS_TABLE",            "sentinel-incidents")
os.environ.setdefault("VALIDATION_RESULTS_TABLE",   "sentinel-validation-results")
os.environ.setdefault("EVENTS_STREAM",              "sentinel-events")
os.environ.setdefault("KSERVE_ENDPOINT",            "http://localhost:8081")
os.environ.setdefault("KSERVE_MODEL",               "llama3.2:1b")
os.environ.setdefault("GITHUB_TOKEN",               "demo-token")
os.environ.setdefault("GITHUB_REPO",                "demo-org/demo-repo")
os.environ.setdefault("RAG_ENDPOINT",               "http://localhost:9999")  # not used locally

# SQS queue URLs (set after bootstrap)
_sqs_client = boto3.client("sqs", **FLOCI)
try:
    _VAL_Q   = _sqs_client.get_queue_url(QueueName="sentinel-validation-jobs")["QueueUrl"]
    _CONF_Q  = _sqs_client.get_queue_url(QueueName="sentinel-confirmed-incidents")["QueueUrl"]
except Exception:
    _VAL_Q = _CONF_Q = ""

os.environ["VALIDATION_JOBS_QUEUE_URL"]  = _VAL_Q
os.environ["EVENTS_QUEUE_URL"]           = _CONF_Q

# Kinesis shard iterator cache: {stream: iterator}
_iterators: dict[str, str] = {}

# Log file location per service — overridable at startup via env
# Format: SERVICE_LOG_<NAME>=path  e.g. SERVICE_LOG_AUTH_SERVICE=/var/log/auth.log
_SERVICE_LOG: dict[str, str] = {
    "auth-service":     os.environ.get("SERVICE_LOG_AUTH_SERVICE",     "/tmp/auth-service.log"),
    "payments-service": os.environ.get("SERVICE_LOG_PAYMENTS_SERVICE", "/tmp/payments-service.log"),
    "search-api":       os.environ.get("SERVICE_LOG_SEARCH_API",       "/tmp/search-api.log"),
}

# GitHub source config — set these env vars to point at your real repo
# GITHUB_REPO   = "owner/repo"  (required for GitHub fetch)
# GITHUB_TOKEN  = "ghp_..."     (optional for public repos, required for private)
# GITHUB_BRANCH = "main"        (default: main)
_GITHUB_REPO   = os.environ.get("GITHUB_REPO",   "")
_GITHUB_TOKEN  = os.environ.get("GITHUB_TOKEN",  "")
_GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")

# Local fallback map — only used when GITHUB_REPO is not set.
# Maps service name → local file path relative to ROOT.
_LOCAL_SRC_FALLBACK: dict[str, Path] = {
    "auth-service":     ROOT / "target/services/auth/app.py",
    "payments-service": ROOT / "target/services/payments/app.py",
    "search-api":       ROOT / "target/services/search/app.py",
}

R    = "\033[0m"
G    = "\033[92m"
Y    = "\033[93m"
B    = "\033[96m"
RED  = "\033[91m"
BOLD = "\033[1m"


# ── Kinesis helpers ───────────────────────────────────────────────────────────

def _get_iterator(stream: str) -> str:
    if stream not in _iterators:
        kc = boto3.client("kinesis", **FLOCI)
        shards = kc.list_shards(StreamName=stream)["Shards"]
        _iterators[stream] = kc.get_shard_iterator(
            StreamName=stream,
            ShardId=shards[0]["ShardId"],
            ShardIteratorType="LATEST",
        )["ShardIterator"]
    return _iterators[stream]


def _poll_stream(stream: str) -> list[dict]:
    kc = boto3.client("kinesis", **FLOCI)
    try:
        it = _get_iterator(stream)
        resp = kc.get_records(ShardIterator=it, Limit=10)
        _iterators[stream] = resp["NextShardIterator"]
        records = []
        for r in resp["Records"]:
            try:
                records.append(json.loads(r["Data"]))
            except Exception:
                pass
        return records
    except Exception as e:
        _iterators.pop(stream, None)
        return []


# ── Lambda handler imports ────────────────────────────────────────────────────

def _import_handlers():
    """Import Lambda handlers lazily to avoid import errors at startup."""
    # Patch sys.path for shared aws_clients
    shared = str(ROOT / "services" / "shared")
    if shared not in sys.path:
        sys.path.insert(0, shared)
    return True


def _run_validator(alert: dict) -> dict:
    """Call the validator Lambda handler with a mock Kinesis record."""
    from services.validator.handler import _validate_alert
    return _validate_alert(alert)


def _run_log_analyzer(batch: dict) -> None:
    """Call the log-analyzer Lambda handler with a mock Kinesis record."""
    try:
        from services.log_analyzer.handler import _analyze_log_batch
        _analyze_log_batch(batch)
    except Exception as e:
        _log(f"log-analyzer error: {e}", Y)


def _ts() -> str:
    return f"\033[90m{datetime.now().strftime('%H:%M:%S')}{R}"


def _log(msg: str, color: str = ""):
    print(f"  {_ts()} {color}{msg}{R}", flush=True)


# ── Context gathering ─────────────────────────────────────────────────────────

def _fetch_github_source(service: str) -> tuple[str, str]:
    """Search a GitHub repo for source files belonging to this service and return
    (raw_source, file_path).  Returns ('', '') if nothing is found or GitHub is
    not configured.

    Strategy:
    1. Walk the repo tree via GitHub API (cached for 60 s per repo/branch).
    2. Score every file by how closely its path matches the service name.
    3. Fetch the top-scoring file and return its decoded content.
    """
    if not _GITHUB_REPO:
        return "", ""

    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if _GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {_GITHUB_TOKEN}"

    api = "https://api.github.com"

    # -- resolve branch → commit SHA so we can cache the tree --
    try:
        branch_url = f"{api}/repos/{_GITHUB_REPO}/branches/{_GITHUB_BRANCH}"
        sha = requests.get(branch_url, headers=headers, timeout=10).json()["commit"]["sha"]
    except Exception as e:
        _log(f"GitHub branch lookup failed: {e}", Y)
        return "", ""

    # -- fetch the full tree (recursive) --
    try:
        tree_url = f"{api}/repos/{_GITHUB_REPO}/git/trees/{sha}?recursive=1"
        tree = requests.get(tree_url, headers=headers, timeout=15).json().get("tree", [])
    except Exception as e:
        _log(f"GitHub tree fetch failed: {e}", Y)
        return "", ""

    # -- score every blob (file) against the service name --
    # Normalise service name: "auth-service" → {"auth", "service"}
    svc_tokens = set(service.lower().replace("-", " ").replace("_", " ").split())
    # file extensions we care about
    src_exts = {".py", ".js", ".ts", ".go", ".java", ".rb", ".rs", ".cs"}

    def _score(path: str) -> int:
        p = path.lower()
        if not any(p.endswith(ext) for ext in src_exts):
            return 0
        # Skip test files, migrations, generated code
        if any(x in p for x in ("/test", "_test", ".test.", "migration", "generated", "vendor", "node_modules")):
            return 0
        score = 0
        for tok in svc_tokens:
            if tok in p:
                score += 10
        # Prefer shorter paths (closer to the service root), prefer main entry points
        score -= len(path.split("/")) * 2
        for name in ("app", "main", "server", "handler", "service", "index"):
            if path.lower().split("/")[-1].startswith(name):
                score += 5
        return score

    candidates = [(blob["path"], _score(blob["path"]))
                  for blob in tree if blob.get("type") == "blob"]
    candidates.sort(key=lambda x: -x[1])

    # Take up to the top 2 files with a positive score
    top = [(p, s) for p, s in candidates[:5] if s > 0][:2]
    if not top:
        _log(f"GitHub: no source files matched for '{service}' in {_GITHUB_REPO}", Y)
        return "", ""

    # -- fetch file content --
    combined = []
    fetched_paths = []
    for file_path, score in top:
        try:
            content_url = f"{api}/repos/{_GITHUB_REPO}/contents/{file_path}?ref={_GITHUB_BRANCH}"
            resp = requests.get(content_url, headers=headers, timeout=10).json()
            import base64 as _b64
            raw = _b64.b64decode(resp["content"]).decode(errors="replace")
            combined.append(f"# --- {file_path} ---\n{raw}")
            fetched_paths.append(file_path)
        except Exception as e:
            _log(f"GitHub: could not fetch {file_path}: {e}", Y)

    if not combined:
        return "", ""

    _log(f"GitHub: fetched {fetched_paths}", G)
    return "\n\n".join(combined), ", ".join(fetched_paths)


def _extract_fault_section(raw_source: str) -> str:
    """From a raw source file, extract only the lines most likely to explain a
    failure — error handlers, health checks, fault injection code.  Keeps the
    prompt short enough for a small model to process quickly."""
    lines = raw_source.splitlines()
    # Keywords that mark fault-relevant lines
    fault_markers = (
        "error", "Error", "ERROR",
        "exception", "Exception",
        "fail", "Fail",
        "health", "Health",
        "fault", "Fault",
        "status", "Status",
        "500", "503", "502",
        "retry", "timeout", "Timeout",
        "raise ", "panic(", "log.Fatal", "log.Error",
    )
    kept: list[int] = []
    for i, line in enumerate(lines):
        if any(m in line for m in fault_markers):
            # include a window of context around each match
            kept.extend(range(max(0, i - 2), min(i + 8, len(lines))))
    seen: set[int] = set()
    unique = [i for i in kept if not (i in seen or seen.add(i))]  # type: ignore[func-returns-value]
    result = "\n".join(lines[i] for i in unique)
    # Hard cap: 80 lines / ~3000 chars so the prompt stays fast
    capped = "\n".join(result.splitlines()[:80])
    return capped


def _gather_context(service: str) -> tuple[str, str]:
    """Return (recent_error_logs, fault_relevant_source).

    Source priority:
      1. GitHub repo (if GITHUB_REPO env var is set)
      2. Local file fallback (for the built-in demo services)
    Both paths then run _extract_fault_section() to trim the source before
    passing it to the LLM.
    """
    # -- logs: last 20 error lines --
    log_path = _SERVICE_LOG.get(service)
    log_excerpt = ""
    if log_path and Path(log_path).exists():
        lines = Path(log_path).read_text(errors="replace").splitlines()
        error_keywords = ("[ERROR]", "[WARN]", "[FATAL]", "CHAOS INJECTED", '" 500 ', '" 502 ', '" 503 ')
        relevant = [l for l in lines if any(k in l for k in error_keywords)]
        log_excerpt = "\n".join(relevant[-20:])

    # -- source: GitHub first, local fallback --
    raw_source = ""
    src_label = ""

    if _GITHUB_REPO:
        raw_source, src_label = _fetch_github_source(service)

    if not raw_source:
        # Fall back to the local demo files
        local = _LOCAL_SRC_FALLBACK.get(service)
        if local and local.exists():
            raw_source = local.read_text(errors="replace")
            src_label = str(local.relative_to(ROOT))

    source = _extract_fault_section(raw_source) if raw_source else ""
    if source:
        _log(f"Source context: {len(source.splitlines())} lines from {src_label or 'local'}", B)

    return log_excerpt, source


# ── Incident creation ─────────────────────────────────────────────────────────

def _create_incident(alert: dict, severity: str) -> str:
    """Create the initial incident record in DynamoDB."""
    db = boto3.resource("dynamodb", **FLOCI)
    iid = alert.get("alert_id", str(uuid.uuid4()))
    svc = alert.get("service_name", "unknown")
    now = datetime.now(timezone.utc).isoformat()
    db.Table("sentinel-incidents").put_item(Item={
        "incident_id":  iid,
        "service_name": svc,
        "severity":     severity,
        "status":       "OPEN",
        "created_at":   now,
        "root_cause":   "⏳ AI analysis in progress…",
        "runbook":      {},
        "log_analysis": {},
        "impact_scope": {
            "error_rate_in_sample": Decimal(str(alert.get("error_rate", 0.1))),
            "users_impacted_pct": 80 if severity == "P1" else 30 if severity == "P2" else 5,
            "summary": alert.get("description", "Incident detected by Sentinel"),
        },
        "metadata": {
            "source":     "chaos-monkey",
            "alert_id":   iid,
            "ai_pending": True,
        },
    })
    return iid


def _trigger_ai_rca(incident_id: str, service: str, severity: str, alert: dict | None = None):
    """Call KServe with real log + source context to generate a grounded RCA."""
    import re

    alert = alert or {}

    # Gather evidence from the actual running service
    log_excerpt, source_code = _gather_context(service)
    has_context = bool(log_excerpt or source_code)
    if has_context:
        _log(f"Context gathered: {len(log_excerpt.splitlines())} error log lines, "
             f"{len(source_code.splitlines())} source lines", B)
    else:
        _log("No log/source context found — using metadata only", Y)

    try:
        _log(f"Calling KServe for RCA ({service}/{severity})…", B)
        kserve = os.environ.get("KSERVE_ENDPOINT", "http://localhost:8081")
        model  = os.environ.get("KSERVE_MODEL", "llama3.2:1b")

        system = (
            "You are an expert SRE diagnosing a live production incident. "
            "You always respond with raw JSON only — no markdown, no explanation, no backticks."
        )

        # Build the evidence block
        evidence_parts = []
        evidence_parts.append(f"Service:     {service}")
        evidence_parts.append(f"Severity:    {severity}")
        if alert.get("error_rate"):
            evidence_parts.append(f"Error rate:  {int(float(alert['error_rate']) * 100)}%")
        if alert.get("p99_latency_ms"):
            evidence_parts.append(f"Latency p99: {alert['p99_latency_ms']}ms")
        if alert.get("description"):
            evidence_parts.append(f"Alert desc:  {alert['description']}")
        incident_block = "\n".join(evidence_parts)

        logs_block   = log_excerpt   if log_excerpt   else "(no error logs captured)"
        source_label = f"from {_GITHUB_REPO}" if _GITHUB_REPO and source_code else "local file"
        source_block = source_code   if source_code   else "(source not available)"

        prompt = f"""A production incident is firing. Use the evidence below to identify the root cause.

== INCIDENT ==
{incident_block}

== RECENT ERROR LOGS ==
{logs_block}

== SERVICE SOURCE CODE ({source_label}) ==
{source_block}

Based on the logs and source code above, identify exactly which function and code path is failing.
Respond with JSON only — no extra text:
{{"root_cause":"<one sentence naming the specific function and failure mode seen in the logs/code>","failed_function":"<exact function name from the source>","runbook":{{"step1":"<action>","step2":"<action>","step3":"<action>","step4":"<action>"}},"degradation_trend":"worsening","affected_components":["{service}"],"summary":"<one sentence describing user impact>"}}"""

        resp = requests.post(
            f"{kserve}/v1/models/{model}:predict",
            json={"inputs": [{"name": "text_input", "shape": [1], "datatype": "BYTES", "data": [prompt]}],
                  "parameters": {"max_new_tokens": 350, "temperature": 0.1, "system": system}},
            timeout=200,
        )
        if resp.ok:
            raw = resp.json()["outputs"][0]["data"][0]
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if match:
                ai = json.loads(match.group())
                if ai.get("root_cause") and len(str(ai["root_cause"])) > 10:
                    er = Decimal(str(0.18 if severity == "P1" else 0.07 if severity == "P2" else 0.02))
                    db = boto3.resource("dynamodb", **FLOCI)
                    db.Table("sentinel-incidents").update_item(
                        Key={"incident_id": incident_id},
                        UpdateExpression=(
                            "SET root_cause=:rc, runbook=:rb, log_analysis=:la, "
                            "impact_scope=:is, ai_generated=:ai, rca_source=:src, "
                            "failed_function=:ff, context_used=:ctx, #m.ai_pending=:p"
                        ),
                        ExpressionAttributeNames={"#m": "metadata"},
                        ExpressionAttributeValues={
                            ":rc":  ai["root_cause"],
                            ":rb":  ai.get("runbook", {}),
                            ":ff":  ai.get("failed_function", "unknown"),
                            ":ctx": "logs+source" if has_context else "metadata-only",
                            ":la":  {
                                "severity":             severity,
                                "rule_severity":        severity,
                                "ml_severity":          severity,
                                "degradation_trend":    ai.get("degradation_trend", "worsening"),
                                "affected_components":  ai.get("affected_components", [service]),
                                "log_sample_size":      len(log_excerpt.splitlines()),
                                "error_rate_in_sample": er,
                                "error_log_excerpt":    log_excerpt[-500:],  # last 500 chars for dashboard
                            },
                            ":is":  {
                                "error_rate_in_sample": er,
                                "users_impacted_pct":   80 if severity == "P1" else 30 if severity == "P2" else 5,
                                "summary":              ai.get("summary", ai["root_cause"][:100]),
                            },
                            ":ai":  True,
                            ":src": "kserve-ollama",
                            ":p":   False,
                        },
                    )
                    _log(f"{G}AI RCA written:{R} {ai['root_cause'][:80]}…")
                    if ai.get("failed_function"):
                        _log(f"{G}Failed function:{R} {ai['failed_function']}")
                    return
    except Exception as e:
        _log(f"KServe RCA failed: {e} — writing fallback", Y)

    # Fallback — still store the raw log lines so the dashboard shows real evidence
    fallback = {
        "P1": "All service connections exhausted under peak load — cascading failures across downstream services.",
        "P2": "Progressive memory growth causing GC pressure and elevated response latency.",
        "P3": "Upstream API rate-limiting causing elevated error rate without full service outage.",
    }
    db = boto3.resource("dynamodb", **FLOCI)
    db.Table("sentinel-incidents").update_item(
        Key={"incident_id": incident_id},
        UpdateExpression=(
            "SET root_cause=:rc, ai_generated=:ai, rca_source=:src, "
            "context_used=:ctx, log_analysis=:la, #m.ai_pending=:p"
        ),
        ExpressionAttributeNames={"#m": "metadata"},
        ExpressionAttributeValues={
            ":rc":  fallback.get(severity, "Service degraded — root cause under investigation."),
            ":ai":  False,
            ":src": "fallback",
            ":ctx": "logs+source" if has_context else "none",
            ":la":  {"error_log_excerpt": log_excerpt[-500:], "log_sample_size": len(log_excerpt.splitlines())},
            ":p":   False,
        },
    )


# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    _import_handlers()

    print(f"\n{BOLD}{B}{'═' * 60}{R}")
    print(f"{BOLD}{B}  Sentinel Pipeline Runner{R}")
    print(f"{BOLD}{B}{'═' * 60}{R}\n")
    print(f"  Polling Kinesis streams every 5s…")
    print(f"  Dashboard: http://localhost:8501")
    print(f"  Ctrl+C to stop\n")

    processed_alerts:  set[str] = set()
    processed_logs:    set[str] = set()

    while True:
        # ── 1. Process sentinel-alerts → validator ────────────────────────────
        for alert in _poll_stream("sentinel-alerts"):
            alert_id = alert.get("alert_id", "")
            if alert_id in processed_alerts:
                continue
            processed_alerts.add(alert_id)

            svc = alert.get("service_name", "unknown")
            sev = alert.get("severity", "P3")
            _log(f"{RED}ALERT{R} received: {sev}/{svc} ({alert_id[:8]}…)")

            try:
                result = _run_validator(alert)
                is_real = result.get("is_real_incident") == "true"
                signals_failed = result.get("signals_failed", 0)
                _log(f"Validator: real={is_real}  signals_failed={signals_failed}/{result.get('signals_checked',0)}")

                if is_real:
                    _log(f"{G}CONFIRMED{R} — creating incident in DynamoDB…")
                    iid = _create_incident(alert, sev)
                    _log(f"Incident created: {iid[:8]}…  severity={sev}  service={svc}")
                    # Trigger AI RCA in background thread — pass full alert for context
                    import threading
                    t = threading.Thread(target=_trigger_ai_rca, args=(iid, svc, sev, alert), daemon=True)
                    t.start()
                else:
                    _log(f"{Y}FALSE POSITIVE{R} — {svc} alert dismissed after validation")

            except Exception as e:
                _log(f"Validator error: {e}", RED)

        # ── 2. Process sentinel-logs → log-analyzer ───────────────────────────
        for batch in _poll_stream("sentinel-logs"):
            batch_id = batch.get("incident_id", "")
            if batch_id in processed_logs:
                continue
            processed_logs.add(batch_id)

            svc   = batch.get("service_name", "?")
            nlogs = len(batch.get("logs", []))
            if nlogs > 0:
                _log(f"Logs: {nlogs} entries from {svc} — running log-analyzer…")
                try:
                    _run_log_analyzer(batch)
                except Exception as e:
                    _log(f"log-analyzer error: {e}", Y)

        time.sleep(5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  Pipeline stopped.\n")
        sys.exit(0)

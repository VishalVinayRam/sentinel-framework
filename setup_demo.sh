#!/usr/bin/env bash
# =============================================================================
#  Sentinel — full demo setup (run once from the project root)
#
#  What this does, in order:
#    1. Check prerequisites
#    2. Install Python dependencies
#    3. Start Floci if it isn't running
#    4. Create all AWS resources in Floci (streams, queues, tables)
#    5. Seed realistic demo incidents + validation records
#    6. Start the dashboard at http://localhost:8501
#
#  Usage:
#    chmod +x setup_demo.sh
#    ./setup_demo.sh
#
#  Re-run safely — every step is idempotent.
# =============================================================================
set -euo pipefail

# ── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

ok()   { echo -e "  ${GREEN}✓${RESET}  $*"; }
info() { echo -e "  ${CYAN}→${RESET}  $*"; }
warn() { echo -e "  ${YELLOW}!${RESET}  $*"; }
die()  { echo -e "  ${RED}✗${RESET}  $*" >&2; exit 1; }
step() { echo -e "\n${BOLD}${CYAN}── $* ${RESET}"; }

# ── Constants ─────────────────────────────────────────────────────────────────
FLOCI_ENDPOINT="http://localhost:4566"
AWS_REGION="us-east-1"
DASHBOARD_PORT=8501
KSERVE_PORT=8080
OLLAMA_MODEL="${SENTINEL_OLLAMA_MODEL:-phi3:mini}"   # override: SENTINEL_OLLAMA_MODEL=tinyllama ./setup_demo.sh
PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"

export FLOCI_ENDPOINT AWS_REGION
export AWS_DEFAULT_REGION="$AWS_REGION"
export AWS_ACCESS_KEY_ID="test"
export AWS_SECRET_ACCESS_KEY="test"
export INCIDENTS_TABLE="sentinel-incidents"
export VALIDATION_RESULTS_TABLE="sentinel-validation-results"
export PR_REVIEWS_TABLE="sentinel-pr-reviews"
export KSERVE_ENDPOINT="http://localhost:${KSERVE_PORT}"
export KSERVE_MODEL="$OLLAMA_MODEL"

# ── Banner ────────────────────────────────────────────────────────────────────
clear
echo -e "${BOLD}"
echo "  ┌─────────────────────────────────────────────────────┐"
echo "  │                                                     │"
echo "  │    ███████╗███████╗███╗   ██╗████████╗██╗███╗  ██╗ │"
echo "  │    ██╔════╝██╔════╝████╗  ██║╚══██╔══╝██║████╗ ██║ │"
echo "  │    ███████╗█████╗  ██╔██╗ ██║   ██║   ██║██╔██╗██║ │"
echo "  │    ╚════██║██╔══╝  ██║╚██╗██║   ██║   ██║██║╚████║ │"
echo "  │    ███████║███████╗██║ ╚████║   ██║   ██║██║ ╚███║ │"
echo "  │    ╚══════╝╚══════╝╚═╝  ╚═══╝   ╚═╝   ╚═╝╚═╝  ╚══╝ │"
echo "  │                                                     │"
echo "  │    AI-Powered Incident Response Framework           │"
echo "  │    Demo Setup Script                                │"
echo "  │                                                     │"
echo "  └─────────────────────────────────────────────────────┘"
echo -e "${RESET}"
sleep 0.5

# ═════════════════════════════════════════════════════════════════════════════
# STEP 1 — Prerequisites
# ═════════════════════════════════════════════════════════════════════════════
step "Step 1/7 — Checking prerequisites"

# Python
python3 --version &>/dev/null || die "Python 3 not found. Install Python 3.10+"
PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
ok "Python $PY_VER"

# pip
pip --version &>/dev/null || die "pip not found"
ok "pip"

# Docker (optional — only needed if Floci isn't already running)
if command -v docker &>/dev/null; then
  ok "Docker $(docker --version | grep -oP '\d+\.\d+\.\d+' | head -1)"
else
  warn "Docker not found — Floci must already be running at $FLOCI_ENDPOINT"
fi

# curl
command -v curl &>/dev/null || die "curl not found"
ok "curl"

# ═════════════════════════════════════════════════════════════════════════════
# STEP 2 — Python dependencies
# ═════════════════════════════════════════════════════════════════════════════
step "Step 2/7 — Installing Python dependencies"

pip install --quiet --upgrade \
  boto3 \
  fastapi \
  "uvicorn[standard]" \
  pyyaml \
  click \
  requests \
  psycopg2-binary 2>/dev/null || true

ok "boto3, fastapi, uvicorn, pyyaml, click, requests"

# ═════════════════════════════════════════════════════════════════════════════
# STEP 3 — Start Floci if not running
# ═════════════════════════════════════════════════════════════════════════════
step "Step 3/7 — Floci (local AWS emulator)"

if curl -sf "$FLOCI_ENDPOINT/_localstack/health" &>/dev/null; then
  FLOCI_VER=$(curl -sf "$FLOCI_ENDPOINT/_localstack/health" | python3 -c "import sys,json; print(json.load(sys.stdin).get('version','?'))" 2>/dev/null || echo "?")
  ok "Floci $FLOCI_VER already running at $FLOCI_ENDPOINT"
else
  info "Floci not running — starting with Docker…"
  if ! command -v docker &>/dev/null; then
    die "Docker required to start Floci. Run: docker pull floci/floci && docker run -d -p 4566:4566 floci/floci"
  fi

  # Try floci first, fall back to localstack
  if docker image inspect floci/floci &>/dev/null 2>&1; then
    docker run -d --name sentinel-floci -p 4566:4566 floci/floci &>/dev/null
  else
    info "floci/floci image not found — trying localstack/localstack"
    docker run -d --name sentinel-floci -p 4566:4566 \
      -e SERVICES=kinesis,sqs,dynamodb,s3,secretsmanager \
      localstack/localstack &>/dev/null
  fi

  info "Waiting for Floci to be ready…"
  for i in $(seq 1 30); do
    curl -sf "$FLOCI_ENDPOINT/_localstack/health" &>/dev/null && break
    sleep 2
    printf "."
  done
  echo ""
  curl -sf "$FLOCI_ENDPOINT/_localstack/health" &>/dev/null || die "Floci failed to start after 60 seconds"
  ok "Floci started"
fi

# ═════════════════════════════════════════════════════════════════════════════
# STEP 4 — Ollama + local KServe bridge (real AI inference)
# ═════════════════════════════════════════════════════════════════════════════
step "Step 4/7 — Local AI (Ollama + KServe bridge)"

OLLAMA_OK=false

if command -v ollama &>/dev/null; then
  # Make sure the Ollama daemon is running
  if ! curl -sf http://localhost:11434/api/tags &>/dev/null; then
    info "Starting Ollama daemon…"
    ollama serve &>/dev/null &
    OLLAMA_SERVE_PID=$!
    for i in $(seq 1 15); do
      curl -sf http://localhost:11434/api/tags &>/dev/null && break
      sleep 1
    done
  fi

  if curl -sf http://localhost:11434/api/tags &>/dev/null; then
    ok "Ollama daemon running at http://localhost:11434"

    # Pick the best available instruction-following model.
    # Coding models (deepseek-coder, codellama, starcoder) won't follow JSON prompts.
    PREFERRED_MODELS="phi3:mini phi3 llama3.2:3b llama3.2:1b llama3 mistral:7b mistral gemma2:2b gemma:2b qwen2:7b qwen2:1.5b"
    AVAILABLE_MODELS=$(ollama list 2>/dev/null | tail -n +2 | awk '{print $1}')

    if [ -n "$AVAILABLE_MODELS" ]; then
      for candidate in $PREFERRED_MODELS; do
        base="${candidate%%:*}"
        if echo "$AVAILABLE_MODELS" | grep -q "^${base}"; then
          FOUND=$(echo "$AVAILABLE_MODELS" | grep "^${base}" | head -1)
          OLLAMA_MODEL="$FOUND"
          ok "Using installed model: $OLLAMA_MODEL"
          break
        fi
      done
    fi

    # If nothing suitable is installed, pull phi3:mini
    if ! echo "$AVAILABLE_MODELS" | grep -qE "^(phi3|llama3|mistral|gemma|qwen2)[^-]"; then
      info "No general-purpose instruction model found. Pulling phi3:mini (~2.3 GB)…"
      if ollama pull phi3:mini; then
        OLLAMA_MODEL="phi3:mini"
        ok "phi3:mini pulled successfully"
      else
        warn "Model pull failed — AI will use fallback RCA text"
        OLLAMA_MODEL=""
      fi
    fi

    # Kill any existing KServe bridge on this port
    fuser -k ${KSERVE_PORT}/tcp &>/dev/null || true
    sleep 0.3

    export KSERVE_MODEL="$OLLAMA_MODEL"
    info "Starting KServe bridge on port $KSERVE_PORT (model: $OLLAMA_MODEL)…"
    OLLAMA_MODEL="$OLLAMA_MODEL" \
    PYTHONPATH="$PROJECT_ROOT/services/kserve-local" \
      uvicorn server:app \
        --app-dir "$PROJECT_ROOT/services/kserve-local" \
        --host 0.0.0.0 --port "$KSERVE_PORT" \
        --log-level warning \
      &>/tmp/sentinel-kserve.log &
    KSERVE_PID=$!

    # Wait for bridge to be ready
    for i in $(seq 1 10); do
      curl -sf "http://localhost:${KSERVE_PORT}/v2/health/ready" &>/dev/null && break
      sleep 1
    done

    if curl -sf "http://localhost:${KSERVE_PORT}/v2/health/ready" &>/dev/null; then
      ok "KServe bridge running at http://localhost:${KSERVE_PORT} (model: $OLLAMA_MODEL)"
      OLLAMA_OK=true
    else
      warn "KServe bridge didn't start — check /tmp/sentinel-kserve.log"
      warn "Demo will use fallback RCA text instead of live AI"
    fi
  else
    warn "Ollama installed but daemon didn't start — skipping AI step"
    warn "Demo will use fallback RCA text instead of live AI"
  fi
else
  warn "Ollama not installed — demo will use fallback RCA text"
  warn "To enable live AI: https://ollama.com  →  ollama pull $OLLAMA_MODEL"
  info "Then re-run: ./setup_demo.sh"
fi

# ═════════════════════════════════════════════════════════════════════════════
# STEP 5 — Create AWS resources in Floci
# ═════════════════════════════════════════════════════════════════════════════
step "Step 5/7 — Provisioning AWS resources in Floci"

python3 - <<'PYEOF'
import boto3, sys

ENDPOINT = "http://localhost:4566"
REGION   = "us-east-1"
KW = dict(endpoint_url=ENDPOINT, region_name=REGION, aws_access_key_id="test", aws_secret_access_key="test")

def ok(msg):   print(f"  \033[32m✓\033[0m  {msg}")
def skip(msg): print(f"  \033[33m–\033[0m  {msg} (already exists)")
def err(msg):  print(f"  \033[31m✗\033[0m  {msg}", file=sys.stderr)

# Kinesis streams
kc = boto3.client("kinesis", **KW)
raw_streams = kc.list_streams().get("StreamNames", [])
existing_streams = {s if isinstance(s, str) else s["StreamName"] for s in raw_streams}
for name in ["sentinel-alerts", "sentinel-events", "sentinel-logs"]:
    if name in existing_streams:
        skip(f"Kinesis: {name}")
    else:
        try:
            kc.create_stream(StreamName=name, ShardCount=1)
            ok(f"Kinesis: {name}")
        except Exception as e:
            skip(f"Kinesis: {name}")

# SQS queues
sc = boto3.client("sqs", **KW)
existing_qs = {u.split("/")[-1] for u in sc.list_queues().get("QueueUrls", [])}
for name in ["sentinel-validation-jobs", "sentinel-confirmed-incidents"]:
    if name in existing_qs:
        skip(f"SQS: {name}")
    else:
        sc.create_queue(QueueName=name)
        ok(f"SQS: {name}")

# DynamoDB tables
db = boto3.client("dynamodb", **KW)
existing_tables = set(db.list_tables()["TableNames"])
tables = [
    {
        "TableName": "sentinel-incidents",
        "KeySchema": [{"AttributeName": "incident_id", "KeyType": "HASH"}],
        "AttributeDefinitions": [
            {"AttributeName": "incident_id", "AttributeType": "S"},
            {"AttributeName": "severity",    "AttributeType": "S"},
            {"AttributeName": "service_name","AttributeType": "S"},
        ],
        "GlobalSecondaryIndexes": [{
            "IndexName": "severity-index",
            "KeySchema": [
                {"AttributeName": "severity",    "KeyType": "HASH"},
                {"AttributeName": "service_name","KeyType": "RANGE"},
            ],
            "Projection": {"ProjectionType": "ALL"},
            "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
        }],
        "BillingMode": "PAY_PER_REQUEST",
    },
    {
        "TableName": "sentinel-validation-results",
        "KeySchema": [{"AttributeName": "alert_id", "KeyType": "HASH"}],
        "AttributeDefinitions": [{"AttributeName": "alert_id", "AttributeType": "S"}],
        "BillingMode": "PAY_PER_REQUEST",
    },
    {
        "TableName": "sentinel-pr-reviews",
        "KeySchema": [{"AttributeName": "pr_id", "KeyType": "HASH"}],
        "AttributeDefinitions": [{"AttributeName": "pr_id", "AttributeType": "S"}],
        "BillingMode": "PAY_PER_REQUEST",
    },
]
dbr = boto3.resource("dynamodb", **KW)
for spec in tables:
    name = spec["TableName"]
    if name in existing_tables:
        skip(f"DynamoDB: {name}")
    else:
        dbr.create_table(**spec)
        ok(f"DynamoDB: {name}")
PYEOF

# ═════════════════════════════════════════════════════════════════════════════
# STEP 5 — Seed demo data
# ═════════════════════════════════════════════════════════════════════════════
step "Step 6/7 — Seeding demo data"

python3 - <<'PYEOF'
import boto3, uuid, sys
from datetime import datetime, timezone, timedelta
from decimal import Decimal

ENDPOINT = "http://localhost:4566"
REGION   = "us-east-1"
KW = dict(endpoint_url=ENDPOINT, region_name=REGION, aws_access_key_id="test", aws_secret_access_key="test")
db = boto3.resource("dynamodb", **KW)

def ok(msg):   print(f"  \033[32m✓\033[0m  {msg}")
def info(msg): print(f"  \033[36m→\033[0m  {msg}")

# Check if already seeded
existing = db.Table("sentinel-incidents").scan(Limit=5).get("Items", [])
demo_items = [i for i in existing if i.get("metadata", {}).get("source") == "demo-seed"]
if len(demo_items) >= 5:
    info("Demo data already present — skipping seed")
    sys.exit(0)

info("Seeding incidents, validation records, and PR reviews…")

now = datetime.now(timezone.utc)

INCIDENTS = [
    {
        "incident_id": str(uuid.uuid4()),
        "service_name": "auth-service",
        "severity": "P1",
        "status": "OPEN",
        "created_at": (now - timedelta(hours=2, minutes=14)).isoformat(),
        "root_cause": (
            "Database connection pool exhausted — all 100 connections saturated under peak load. "
            "Root cause: a missing composite index on the sessions table caused full-table scans "
            "that held row locks for 30+ seconds, cascading into timeout failures across every "
            "service that depends on auth. Connection wait queue backed up to 2,400 requests."
        ),
        "runbook": {
            "step1": "Scale out read replicas: kubectl scale deployment auth-db-replica --replicas=3",
            "step2": "Kill long-running queries: SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE duration > interval '5 seconds'",
            "step3": "Add missing index: CREATE INDEX CONCURRENTLY idx_sessions_user_created ON sessions(user_id, created_at)",
            "step4": "Drain and restart connection pool: kubectl rollout restart deployment auth-service",
            "step5": "Verify p99 latency drops below 200ms in Grafana before closing",
        },
        "log_analysis": {
            "severity": "P1", "rule_severity": "P1", "ml_severity": "P1",
            "degradation_trend": "worsening",
            "affected_components": ["auth-service", "postgres-primary", "session-cache", "api-gateway"],
            "log_sample_size": 3200,
            "error_rate_in_sample": Decimal("0.91"),
            "analyzed_at": (now - timedelta(hours=2, minutes=10)).isoformat(),
        },
        "impact_scope": {
            "error_rate_in_sample": Decimal("0.91"),
            "users_impacted_pct": 95,
            "summary": "Full auth outage — 95% of login attempts failing, all downstream services affected",
        },
        "metadata": {"source": "demo-seed", "team": "platform"},
    },
    {
        "incident_id": str(uuid.uuid4()),
        "service_name": "payments-service",
        "severity": "P2",
        "status": "OPEN",
        "created_at": (now - timedelta(hours=5, minutes=33)).isoformat(),
        "root_cause": (
            "Memory leak in the JWT token refresh cache — LRU eviction not triggering correctly "
            "after the v2.4.1 config change that doubled the cache TTL from 15 to 30 minutes. "
            "Cache grew from a baseline of 200MB to 1.8GB over 6 hours until the pod was OOM-killed. "
            "Three restart loops observed. The leak is in TokenCache.refresh() which allocates a "
            "new buffer per call without releasing the previous one when the token hasn't changed."
        ),
        "runbook": {
            "step1": "Restart affected pods immediately: kubectl rollout restart deployment payments-service",
            "step2": "Revert cache TTL config: kubectl set env deployment/payments-service TOKEN_CACHE_TTL=900",
            "step3": "Monitor memory usage in Grafana — alert if >600MB",
            "step4": "Deploy fix for TokenCache.refresh() memory leak (PR #1847)",
            "step5": "Run load test in staging to confirm fix before prod promotion",
        },
        "log_analysis": {
            "severity": "P2", "rule_severity": "P2", "ml_severity": "P2",
            "degradation_trend": "worsening",
            "affected_components": ["payments-service", "token-cache", "billing-api"],
            "log_sample_size": 1800,
            "error_rate_in_sample": Decimal("0.34"),
            "analyzed_at": (now - timedelta(hours=5, minutes=20)).isoformat(),
        },
        "impact_scope": {
            "error_rate_in_sample": Decimal("0.34"),
            "users_impacted_pct": 28,
            "summary": "Payment flows degraded for ~28% of users, checkout success rate dropped from 99.1% to 71.4%",
        },
        "metadata": {"source": "demo-seed", "team": "payments"},
    },
    {
        "incident_id": str(uuid.uuid4()),
        "service_name": "search-api",
        "severity": "P3",
        "status": "OPEN",
        "created_at": (now - timedelta(hours=1, minutes=8)).isoformat(),
        "root_cause": (
            "Upstream rate limiting from the Elasticsearch cluster — 429 responses started after "
            "the nightly index rebuild job began running bulk queries without backoff. The job "
            "saturated the cluster's write thread pool, causing read latency to spike from 12ms "
            "to 340ms. No user-visible failures yet but the error budget is at 38% for the hour."
        ),
        "runbook": {
            "step1": "Throttle the index rebuild job: UPDATE job_config SET max_rps=50 WHERE job='nightly-reindex'",
            "step2": "Add exponential backoff with jitter to the bulk indexer",
            "step3": "Coordinate with data-eng to reschedule rebuild to 3am UTC (off-peak)",
        },
        "log_analysis": {
            "severity": "P3", "rule_severity": "P3", "ml_severity": "P3",
            "degradation_trend": "stable",
            "affected_components": ["search-api", "elasticsearch", "reindex-job"],
            "log_sample_size": 620,
            "error_rate_in_sample": Decimal("0.07"),
            "analyzed_at": (now - timedelta(hours=1)).isoformat(),
        },
        "impact_scope": {
            "error_rate_in_sample": Decimal("0.07"),
            "users_impacted_pct": 4,
            "summary": "Search latency elevated for ~4% of queries, no hard failures yet",
        },
        "metadata": {"source": "demo-seed", "team": "search"},
    },
    {
        "incident_id": str(uuid.uuid4()),
        "service_name": "notification-service",
        "severity": "P3",
        "status": "RESOLVED",
        "created_at": (now - timedelta(days=1, hours=3)).isoformat(),
        "resolved_at": (now - timedelta(days=1, hours=1, minutes=22)).isoformat(),
        "root_cause": (
            "Email delivery failures due to SendGrid API key rotation not propagating to all pods. "
            "Pods that hadn't restarted since last Tuesday were still using the revoked key. "
            "Rolling restart after the secret update resolved the issue. Notification delivery "
            "recovered to 99.8% within 8 minutes of the restart completing."
        ),
        "runbook": {
            "step1": "Update Kubernetes secret: kubectl create secret generic sendgrid-creds --from-literal=api_key=NEW_KEY --dry-run=client -o yaml | kubectl apply -f -",
            "step2": "Rolling restart: kubectl rollout restart deployment notification-service",
            "step3": "Monitor delivery rate in SendGrid dashboard — confirm >99% within 10 min",
            "step4": "Add secret rotation webhook to auto-trigger rolling restart in future",
        },
        "log_analysis": {
            "severity": "P3", "rule_severity": "P2", "ml_severity": "P3",
            "degradation_trend": "improving",
            "affected_components": ["notification-service", "sendgrid-gateway"],
            "log_sample_size": 290,
            "error_rate_in_sample": Decimal("0.43"),
            "analyzed_at": (now - timedelta(days=1, hours=2, minutes=50)).isoformat(),
        },
        "impact_scope": {
            "error_rate_in_sample": Decimal("0.43"),
            "users_impacted_pct": 12,
            "summary": "Email notifications delayed or dropped for 12% of users for ~98 minutes",
        },
        "metadata": {"source": "demo-seed", "team": "platform"},
    },
    {
        "incident_id": str(uuid.uuid4()),
        "service_name": "ml-inference",
        "severity": "P2",
        "status": "RESOLVED",
        "created_at": (now - timedelta(days=2, hours=6)).isoformat(),
        "resolved_at": (now - timedelta(days=2, hours=4, minutes=11)).isoformat(),
        "root_cause": (
            "GPU memory fragmentation on the KServe inference nodes — after 72 hours of continuous "
            "operation, CUDA memory allocations fragmented to the point where new model requests "
            "could not get contiguous 4GB blocks despite 12GB free. Restarting the inference pods "
            "defragmented GPU memory and restored normal operation."
        ),
        "runbook": {
            "step1": "Identify affected nodes: kubectl get pods -n ml-ops-lite -o wide | grep inference",
            "step2": "Cordon nodes to stop new scheduling: kubectl cordon NODE_NAME",
            "step3": "Drain inference pods: kubectl drain NODE_NAME --ignore-daemonsets --delete-emptydir-data",
            "step4": "Restart KServe inference server pods",
            "step5": "Schedule weekly pod restarts via CronJob to prevent recurrence",
        },
        "log_analysis": {
            "severity": "P2", "rule_severity": "P2", "ml_severity": "P1",
            "degradation_trend": "improving",
            "affected_components": ["ml-inference", "kserve", "gpu-node-pool"],
            "log_sample_size": 512,
            "error_rate_in_sample": Decimal("0.62"),
            "analyzed_at": (now - timedelta(days=2, hours=5, minutes=45)).isoformat(),
        },
        "impact_scope": {
            "error_rate_in_sample": Decimal("0.62"),
            "users_impacted_pct": 40,
            "summary": "ML inference failing for 40% of requests — severity scoring and RCA pipeline degraded",
        },
        "metadata": {"source": "demo-seed", "team": "ml-ops"},
    },
    {
        "incident_id": str(uuid.uuid4()),
        "service_name": "worker-service",
        "severity": "P4",
        "status": "OPEN",
        "created_at": (now - timedelta(minutes=38)).isoformat(),
        "root_cause": (
            "Verbose DEBUG logging accidentally left enabled after last Tuesday's hotfix for the "
            "job scheduler. Log volume increased 40x — from 2MB/min to 82MB/min. No performance "
            "impact observed, but CloudWatch ingestion costs will spike by ~$340 at month end "
            "if not reverted."
        ),
        "runbook": {
            "step1": "Set log level: kubectl set env deployment/worker-service LOG_LEVEL=INFO",
            "step2": "Redeploy: kubectl rollout restart deployment worker-service",
            "step3": "Verify log volume drops in CloudWatch Logs Insights",
            "step4": "Add log-level lint check to CI to prevent recurrence",
        },
        "log_analysis": {
            "severity": "P4", "rule_severity": "P4", "ml_severity": "P4",
            "degradation_trend": "stable",
            "affected_components": ["worker-service"],
            "log_sample_size": 8400,
            "error_rate_in_sample": Decimal("0.001"),
            "analyzed_at": (now - timedelta(minutes=30)).isoformat(),
        },
        "impact_scope": {
            "error_rate_in_sample": Decimal("0.001"),
            "users_impacted_pct": 0,
            "summary": "No user impact. Cost impact only — excessive CloudWatch log ingestion",
        },
        "metadata": {"source": "demo-seed", "team": "backend"},
    },
]

# Seed incidents
inc_table = db.Table("sentinel-incidents")
with inc_table.batch_writer() as batch:
    for inc in INCIDENTS:
        batch.put_item(Item=inc)
ok(f"Seeded {len(INCIDENTS)} incidents (P1×1 open, P2×1 open + 1 resolved, P3×1 open + 1 resolved, P4×1 open)")

# Seed validation results
val_table = db.Table("sentinel-validation-results")
VALIDATIONS = [
    {"alert_id": str(uuid.uuid4()), "service_name": "auth-service",          "is_real_incident": "true",  "signals_checked": 3, "signals_failed": 3, "validated_at": (now-timedelta(hours=2,minutes=13)).isoformat()},
    {"alert_id": str(uuid.uuid4()), "service_name": "payments-service",       "is_real_incident": "true",  "signals_checked": 3, "signals_failed": 2, "validated_at": (now-timedelta(hours=5,minutes=32)).isoformat()},
    {"alert_id": str(uuid.uuid4()), "service_name": "search-api",             "is_real_incident": "true",  "signals_checked": 3, "signals_failed": 2, "validated_at": (now-timedelta(hours=1,minutes=7)).isoformat()},
    {"alert_id": str(uuid.uuid4()), "service_name": "worker-service",         "is_real_incident": "true",  "signals_checked": 3, "signals_failed": 1, "validated_at": (now-timedelta(minutes=37)).isoformat()},
    {"alert_id": str(uuid.uuid4()), "service_name": "cache-service",          "is_real_incident": "false", "signals_checked": 3, "signals_failed": 0, "validated_at": (now-timedelta(hours=3,minutes=20)).isoformat()},
    {"alert_id": str(uuid.uuid4()), "service_name": "metrics-exporter",       "is_real_incident": "false", "signals_checked": 3, "signals_failed": 1, "validated_at": (now-timedelta(hours=7)).isoformat()},
    {"alert_id": str(uuid.uuid4()), "service_name": "feature-flag-service",   "is_real_incident": "false", "signals_checked": 3, "signals_failed": 0, "validated_at": (now-timedelta(hours=11)).isoformat()},
    {"alert_id": str(uuid.uuid4()), "service_name": "notification-service",   "is_real_incident": "true",  "signals_checked": 3, "signals_failed": 2, "validated_at": (now-timedelta(days=1,hours=3)).isoformat()},
]
with val_table.batch_writer() as batch:
    for v in VALIDATIONS:
        batch.put_item(Item=v)
ok(f"Seeded {len(VALIDATIONS)} validation records (5 real, 3 false-positive → 37.5% FP rate)")
PYEOF

# ═════════════════════════════════════════════════════════════════════════════
# STEP 6 — Start the dashboard
# ═════════════════════════════════════════════════════════════════════════════
step "Step 7/7 — Starting the Sentinel dashboard"

# Kill any existing dashboard on this port
fuser -k ${DASHBOARD_PORT}/tcp &>/dev/null || true
sleep 0.5

echo ""
AI_STATUS="fallback text (Ollama not running)"
$OLLAMA_OK && AI_STATUS="live AI via Ollama + phi3:mini  ✓"

echo -e "${BOLD}${GREEN}"
echo "  ┌──────────────────────────────────────────────────┐"
echo "  │                                                  │"
echo "  │   ✓  Sentinel is ready!                         │"
echo "  │                                                  │"
echo "  │   Dashboard:  http://localhost:${DASHBOARD_PORT}              │"
echo "  │   KServe AI:  http://localhost:${KSERVE_PORT}               │"
echo "  │   AI engine:  ${AI_STATUS}   │"
echo "  │                                                  │"
echo "  │   Demo flow:                                     │"
echo "  │   1. Overview — stats + open incidents           │"
echo "  │   2. Incidents — click any row for RCA           │"
echo "  │   3. Validation Log — real vs false-positive     │"
echo "  │   4. Demo Mode — fire a live incident            │"
echo "  │      (AI RCA appears in ~30 s via Ollama)        │"
echo "  │                                                  │"
echo "  │   Press Ctrl+C to stop the dashboard             │"
echo "  │                                                  │"
echo "  └──────────────────────────────────────────────────┘"
echo -e "${RESET}"

# Try to open the browser automatically
if command -v xdg-open &>/dev/null; then
  (sleep 2 && xdg-open "http://localhost:${DASHBOARD_PORT}") &
elif command -v open &>/dev/null; then
  (sleep 2 && open "http://localhost:${DASHBOARD_PORT}") &
fi

# Start the server (foreground so Ctrl+C stops it cleanly)
exec uvicorn services.dashboard.api:app \
  --host 0.0.0.0 \
  --port ${DASHBOARD_PORT} \
  --log-level warning

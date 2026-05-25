"""
Sentinel Dashboard API — FastAPI backend.

Reads incidents from DynamoDB (Floci or real AWS) and exposes a REST API
consumed by the single-page dashboard UI.

Run:
    cd /path/to/Project-KEMM
    uvicorn services.dashboard.api:app --reload --port 8501
"""

import json
import os
import re
import sys
import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

import boto3
import requests as _requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

# ── AWS / Floci config ─────────────────────────────────────────────────────
ENDPOINT = os.environ.get("FLOCI_ENDPOINT", "http://localhost:4566")
REGION   = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
IS_LOCAL = bool(os.environ.get("FLOCI_ENDPOINT", ""))

_boto_kwargs = dict(region_name=REGION)
if IS_LOCAL or ENDPOINT != "":
    _boto_kwargs.update(
        endpoint_url=ENDPOINT,
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )

INCIDENTS_TABLE  = os.environ.get("INCIDENTS_TABLE",           "sentinel-incidents")
PR_REVIEWS_TABLE = os.environ.get("PR_REVIEWS_TABLE",          "sentinel-pr-reviews")
VALIDATION_TABLE = os.environ.get("VALIDATION_RESULTS_TABLE",  "sentinel-validation-results")

# KServe / Ollama (local, always tried first — free)
KSERVE_ENDPOINT = os.environ.get("KSERVE_ENDPOINT", "http://localhost:8081")
KSERVE_MODEL    = os.environ.get("KSERVE_MODEL",    "llama3.2:1b")

# Cloud LLM API keys — any combination is valid; chain tries them in order
GEMINI_API_KEY    = os.environ.get("GEMINI_API_KEY",    "")
GEMINI_MODEL      = os.environ.get("GEMINI_MODEL",      "gemini-1.5-flash")
OPENAI_API_KEY    = os.environ.get("OPENAI_API_KEY",    "")
OPENAI_MODEL      = os.environ.get("OPENAI_MODEL",      "gpt-4o-mini")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL   = os.environ.get("ANTHROPIC_MODEL",   "claude-haiku-4-5-20251001")

# Slack — set SLACK_WEBHOOK_URL to get P1/P2 notifications
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")
SLACK_CHANNEL     = os.environ.get("SLACK_CHANNEL",     "")

# Optional log source configuration
LOKI_URL         = os.environ.get("LOKI_URL",   "")          # e.g. http://localhost:3100
LOKI_TENANT_ID   = os.environ.get("LOKI_TENANT_ID",  "")
LOKI_USER        = os.environ.get("LOKI_USER",   "")
LOKI_PASSWORD    = os.environ.get("LOKI_PASSWORD", "")

CLOUDWATCH_LOG_GROUP_PREFIX = os.environ.get("CLOUDWATCH_LOG_GROUP_PREFIX", "/ecs/")

# ── App ────────────────────────────────────────────────────────────────────
app = FastAPI(title="Sentinel Dashboard API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

UI_DIR = Path(__file__).parent / "ui"


def _dynamo():
    return boto3.resource("dynamodb", **_boto_kwargs)


def _kinesis():
    return boto3.client("kinesis", **_boto_kwargs)


def _serialise(item: dict) -> dict:
    """Make a DynamoDB item JSON-serialisable."""
    out = {}
    for k, v in item.items():
        if hasattr(v, "__class__") and "Decimal" in type(v).__name__:
            out[k] = float(v)
        elif isinstance(v, dict):
            out[k] = _serialise(v)
        elif isinstance(v, list):
            out[k] = [_serialise(i) if isinstance(i, dict) else i for i in v]
        else:
            out[k] = v
    return out


# ── Realistic incident title generation ────────────────────────────────────

_TITLE_TEMPLATES = {
    "P1": [
        "{service} — Full Outage / {error_type}",
        "{service} — CriticalError: {error_type} (100% failure rate)",
        "FIRING P1: {service} down — {error_type}",
    ],
    "P2": [
        "{service} — Degraded: {error_type}",
        "{service} — High error rate / {error_type}",
        "FIRING P2: {service} — {error_type}",
    ],
    "P3": [
        "{service} — Elevated latency / {error_type}",
        "{service} — Warning: {error_type}",
        "FIRING P3: {service} / {error_type}",
    ],
    "P4": [
        "{service} — Info: {error_type}",
        "{service} — Notice: {error_type}",
    ],
}

_ERROR_TYPES = {
    "auth-service":          ["DatabaseConnectionPoolExhausted", "SessionTableLockTimeout", "OAuthTokenValidationFailed"],
    "payments-service":      ["PaymentGatewayTimeout", "CheckoutSuccessRateDrop", "TokenCacheMemoryLeak"],
    "search-api":            ["ElasticsearchClusterThrottled", "IndexRebuildJobRateLimitExceeded", "QueryLatencySpike"],
    "notification-service":  ["SMTPDeliveryFailure", "SendGridAPIKeyRevoked", "EmailQueueBacklog"],
    "worker-service":        ["JobQueueDepthCritical", "DeadLetterQueueSpike", "WorkerOOMKilled"],
    "api-gateway":           ["UpstreamTimeoutCascade", "CircuitBreakerOpen", "SSL_CertificateExpiringSoon"],
    "data-pipeline":         ["KinesisShardIteratorExpired", "DLQMessageSpike", "BatchJobStalled"],
    "user-service":          ["RegistrationEndpointDown", "ProfileFetchTimeout", "SessionStoreUnavailable"],
    "inventory-service":     ["StockLevelSyncFailed", "DatabaseReplicationLag", "CacheInvalidationStorm"],
    "recommendation-engine": ["ModelServingTimeout", "FeatureStoreStaleness", "InferenceLatencyP99Spike"],
}

_DEFAULT_ERRORS = ["HighErrorRate", "ServiceDegraded", "LatencySpike", "ConnectionRefused"]

import random as _random


# ── Code context: per-(service, severity) curated source evidence ──────────
# Each entry has 2–3 files that show the actual failing code, misconfigured
# YAML, and the infra component that needs to be enhanced.
# highlight_offsets = 0-indexed line positions within `lines` to mark as bad.

_CODE_CONTEXT_MAP: dict[tuple, dict] = {

    ("auth-service", "P1"): {
        "summary": "Connection pool exhausted — 3 files must change to prevent recurrence.",
        "files": [
            {
                "path": "services/auth/db.py",
                "language": "python", "start_line": 14,
                "lines": [
                    "from sqlalchemy import create_engine",
                    "from sqlalchemy.orm import sessionmaker",
                    "import os",
                    "",
                    "DB_URL = os.environ['DATABASE_URL']",
                    "",
                    "engine = create_engine(",
                    "    DB_URL,",
                    "    pool_size=5,          # only 5 concurrent connections",
                    "    max_overflow=0,       # ← no burst allowed — BUG",
                    "    pool_timeout=30,",
                    "    pool_pre_ping=False,  # ← stale conns not recycled",
                    ")",
                    "Session = sessionmaker(bind=engine)",
                ],
                "highlight_offsets": [9, 11],
                "annotation": "pool_size=5 + max_overflow=0 → any spike above 5 concurrent DB ops raises OperationalError: too many clients.",
                "fix": "engine = create_engine(DB_URL, pool_size=20, max_overflow=10, pool_pre_ping=True, pool_recycle=300)",
            },
            {
                "path": "helm/sentinel/values.yaml",
                "language": "yaml", "start_line": 42,
                "lines": [
                    "auth-service:",
                    "  replicaCount: 2",
                    "  database:",
                    "    pool:",
                    "      max: 5          # ← increase to 20",
                    "      acquireTimeout: 30000",
                    "  resources:",
                    "    limits:",
                    "      cpu: 500m",
                    "      memory: 512Mi  # ← increase to 1Gi",
                ],
                "highlight_offsets": [4, 9],
                "annotation": "Helm values cap pool at 5 and pod memory at 512Mi — OOM-kills compound the connection storm.",
                "fix": "Set database.pool.max: 20 and resources.limits.memory: 1Gi",
            },
            {
                "path": "infra/rds.tf",
                "language": "hcl", "start_line": 28,
                "lines": [
                    'resource "aws_db_parameter_group" "auth" {',
                    '  family = "postgres14"',
                    "  parameter {",
                    '    name  = "max_connections"',
                    '    value = "50"       # ← raise to 200',
                    "  }",
                    "  parameter {",
                    '    name  = "idle_in_transaction_session_timeout"',
                    '    value = "0"        # ← set to 30000 ms',
                    "  }",
                    "}",
                ],
                "highlight_offsets": [4, 8],
                "annotation": "RDS max_connections=50 and zero idle timeout — leaked connections never expire under load.",
                "fix": "max_connections=200, idle_in_transaction_session_timeout=30000",
            },
        ],
    },

    ("auth-service", "P2"): {
        "summary": "Timezone-naive datetime comparison causes LRU eviction to never fire.",
        "files": [
            {
                "path": "services/auth/cache.py",
                "language": "python", "start_line": 28,
                "lines": [
                    "from datetime import datetime, timedelta",
                    "from typing import Optional",
                    "",
                    "_token_cache: dict = {}",
                    "",
                    "def get_token(user_id: str) -> Optional[str]:",
                    "    entry = _token_cache.get(user_id)",
                    "    if entry:",
                    "        # BUG: utcnow() vs now() → TypeError swallowed silently",
                    "        if entry['expires_at'] > datetime.now():  # ← tz mismatch",
                    "            return entry['token']",
                    "    return None",
                    "",
                    "def set_token(user_id: str, token: str, ttl=3600):",
                    "    _token_cache[user_id] = {",
                    "        'token': token,",
                    "        'expires_at': datetime.utcnow() + timedelta(seconds=ttl),  # ← utcnow",
                    "    }",
                    "    # BUG: no eviction — dict grows unbounded",
                ],
                "highlight_offsets": [9, 16, 18],
                "annotation": "datetime.utcnow() stored but compared to datetime.now() — comparison raises TypeError → eviction branch never executes → cache grows to 1.8 GB.",
                "fix": "Use datetime.now(tz=timezone.utc) consistently. Add LRU via functools.lru_cache or cachetools.TTLCache.",
            },
            {
                "path": "helm/sentinel/values.yaml",
                "language": "yaml", "start_line": 54,
                "lines": [
                    "auth-service:",
                    "  env:",
                    "    TOKEN_CACHE_TTL: '3600'",
                    "    TOKEN_CACHE_MAX_SIZE: ''   # ← empty = unlimited",
                    "  resources:",
                    "    limits:",
                    "      memory: 512Mi             # ← OOM-kills at heap 512 MB",
                ],
                "highlight_offsets": [3, 6],
                "annotation": "No upper bound on cache size + 512Mi memory limit → OOM-kill loop every ~6 hours.",
                "fix": "TOKEN_CACHE_MAX_SIZE: '50000' and memory limit: 1.5Gi",
            },
        ],
    },

    ("payments-service", "P1"): {
        "summary": "No retry budget or circuit breaker on the gateway call — 100% failure rate.",
        "files": [
            {
                "path": "services/payments/gateway.py",
                "language": "python", "start_line": 17,
                "lines": [
                    "import requests",
                    "",
                    "GATEWAY_URL = 'https://api.stripe.com/v1'",
                    "",
                    "def charge(amount: int, card_token: str) -> dict:",
                    "    # BUG: no timeout, no retry, no circuit breaker",
                    "    resp = requests.post(       # ← blocks indefinitely",
                    "        f'{GATEWAY_URL}/charges',",
                    "        data={'amount': amount, 'source': card_token},",
                    "        headers={'Authorization': f'Bearer {_key()}'},",
                    "    )",
                    "    resp.raise_for_status()    # ← propagates 5xx directly",
                    "    return resp.json()",
                ],
                "highlight_offsets": [6, 11],
                "annotation": "requests.post with no timeout hangs worker threads. One gateway blip causes thread pool exhaustion across all payment pods.",
                "fix": "Add timeout=5, use tenacity @retry with exponential backoff, wrap in pybreaker CircuitBreaker.",
            },
            {
                "path": "services/payments/config.yaml",
                "language": "yaml", "start_line": 1,
                "lines": [
                    "gateway:",
                    "  url: https://api.stripe.com/v1",
                    "  timeout: null        # ← must be set",
                    "  retry:",
                    "    max_attempts: 1    # ← only 1 attempt, no retry",
                    "    backoff: false     # ← no backoff",
                    "circuit_breaker:",
                    "  enabled: false       # ← completely disabled",
                    "  failure_threshold: 5",
                    "  reset_timeout: 60",
                ],
                "highlight_offsets": [2, 4, 5, 7],
                "annotation": "Gateway config has no timeout, single attempt, no backoff, and circuit breaker disabled.",
                "fix": "timeout: 5, retry.max_attempts: 3, retry.backoff: exponential, circuit_breaker.enabled: true",
            },
        ],
    },

    ("payments-service", "P2"): {
        "summary": "Unbounded in-memory charge queue grows until OOM-kill.",
        "files": [
            {
                "path": "services/payments/processor.py",
                "language": "python", "start_line": 34,
                "lines": [
                    "from datetime import datetime",
                    "",
                    "_pending: list = []   # ← grows unbounded, never cleared",
                    "",
                    "def enqueue(charge_data: dict):",
                    "    _pending.append({",
                    "        **charge_data,",
                    "        'queued_at': datetime.now(),",
                    "        'retries': 0,",
                    "    })",
                    "    # BUG: no max-size guard, no eviction",
                    "",
                    "def flush():",
                    "    for item in _pending:   # ← O(n) full scan each tick",
                    "        _process(item)",
                    "    # BUG: never clears the list after processing",
                ],
                "highlight_offsets": [2, 10, 15],
                "annotation": "_pending list is appended to on every request and never cleared — heap grows ~4 MB/min until the pod is OOM-killed.",
                "fix": "Use collections.deque(maxlen=10000) and clear processed items. Add memory gauge metric.",
            },
            {
                "path": "infra/ecs.tf",
                "language": "hcl", "start_line": 55,
                "lines": [
                    'resource "aws_ecs_task_definition" "payments" {',
                    '  cpu    = "512"',
                    '  memory = "512"   # ← 512 MB is too low',
                    "  container_definitions = jsonencode([{",
                    '    name  = "payments-service"',
                    "    environment = [",
                    '      { name = "WORKER_THREADS", value = "20" },',
                    '      { name = "MAX_QUEUE_DEPTH", value = "" },  # ← unset',
                    "    ]",
                    "  }])",
                    "}",
                ],
                "highlight_offsets": [2, 7],
                "annotation": "ECS task only has 512 MB — 20 worker threads with no queue depth limit fills it in minutes.",
                "fix": "memory: 2048, add MAX_QUEUE_DEPTH=5000 environment variable.",
            },
        ],
    },

    ("search-api", "P3"): {
        "summary": "Bulk indexer sends all documents in a single request with no rate limiting.",
        "files": [
            {
                "path": "services/search/indexer.py",
                "language": "python", "start_line": 22,
                "lines": [
                    "from elasticsearch import Elasticsearch",
                    "",
                    "es = Elasticsearch(ES_URL)",
                    "",
                    "def bulk_index(docs: list[dict]):",
                    "    body = []",
                    "    for doc in docs:",
                    "        body.extend([",
                    "            {'index': {'_index': 'products', '_id': doc['id']}},",
                    "            doc,",
                    "        ])",
                    "    # BUG: sends ALL docs at once, no batching or rate limit",
                    "    es.bulk(body=body, timeout='30s')  # ← can be 500k docs",
                ],
                "highlight_offsets": [11, 12],
                "annotation": "Sends the entire doc corpus in one bulk request — easily exceeds Elasticsearch's 100 MB body limit and request quota.",
                "fix": "Batch in chunks of 500 docs, add time.sleep(0.1) between batches, set requests_per_second=500.",
            },
            {
                "path": "observability/elasticsearch/index-policy.yaml",
                "language": "yaml", "start_line": 1,
                "lines": [
                    "index_patterns: ['products-*']",
                    "settings:",
                    "  number_of_shards: 1",
                    "  number_of_replicas: 1",
                    "  # missing: throttle for bulk operations",
                    "  # missing: slow log thresholds",
                    "mappings:",
                    "  dynamic: true        # ← allows unbounded field explosion",
                ],
                "highlight_offsets": [4, 5, 7],
                "annotation": "No bulk throttle config and dynamic=true lets mapping explosion add thousands of fields, slowing all queries.",
                "fix": "Add index.store.throttle.max_bytes_per_sec: '50mb' and dynamic: 'strict'.",
            },
        ],
    },

    ("api-gateway", "P1"): {
        "summary": "Upstream timeout too short + no circuit breaker → cascade failure.",
        "files": [
            {
                "path": "services/api-gateway/middleware/proxy.py",
                "language": "python", "start_line": 41,
                "lines": [
                    "import httpx",
                    "",
                    "UPSTREAM_TIMEOUT = 1.0   # ← 1s too aggressive for DB-backed routes",
                    "",
                    "async def proxy(request, upstream_url: str):",
                    "    async with httpx.AsyncClient() as client:",
                    "        try:",
                    "            resp = await client.request(",
                    "                request.method, upstream_url,",
                    "                timeout=UPSTREAM_TIMEOUT,   # ← hard 1s cutoff",
                    "                headers=dict(request.headers),",
                    "            )",
                    "        except httpx.TimeoutException:",
                    "            # BUG: immediate 503 — no retry, no fallback",
                    "            raise HTTPException(503, 'upstream timeout')",
                ],
                "highlight_offsets": [2, 9, 13, 14],
                "annotation": "1-second timeout fires on any P99 latency spike. With no retry or circuit breaker, every in-flight request fails immediately.",
                "fix": "UPSTREAM_TIMEOUT=10.0, add httpx retry transport with max_attempts=2, wrap in circuit breaker (pybreaker).",
            },
            {
                "path": "helm/sentinel/values.yaml",
                "language": "yaml", "start_line": 78,
                "lines": [
                    "api-gateway:",
                    "  proxy:",
                    "    upstreamTimeoutMs: 1000   # ← raise to 10000",
                    "    retries: 0                # ← set to 2",
                    "    circuitBreaker:",
                    "      enabled: false          # ← enable",
                    "      failureRateThreshold: 50",
                    "      waitDurationMs: 60000",
                ],
                "highlight_offsets": [2, 3, 5],
                "annotation": "Helm values disable circuit breaker and cap retries at 0 — a single upstream blip cascades to all clients.",
                "fix": "upstreamTimeoutMs: 10000, retries: 2, circuitBreaker.enabled: true",
            },
        ],
    },

    ("data-pipeline", "P2"): {
        "summary": "Kinesis shard iterator expires after 5 min of idle — batch consumer stalls.",
        "files": [
            {
                "path": "services/data-pipeline/consumer.py",
                "language": "python", "start_line": 58,
                "lines": [
                    "import boto3, time",
                    "",
                    "kinesis = boto3.client('kinesis')",
                    "",
                    "def consume(stream: str, shard_id: str):",
                    "    it = kinesis.get_shard_iterator(",
                    "        StreamName=stream,",
                    "        ShardId=shard_id,",
                    "        ShardIteratorType='LATEST',",
                    "    )['ShardIterator']",
                    "    while True:",
                    "        resp = kinesis.get_records(ShardIterator=it, Limit=100)",
                    "        it = resp['NextShardIterator']",
                    "        if not resp['Records']:",
                    "            time.sleep(300)   # ← 5-min sleep expires the iterator",
                    "            continue          # ← next get_records → ExpiredIterator",
                ],
                "highlight_offsets": [14, 15],
                "annotation": "Sleeping 300 s when the shard is empty causes the iterator to expire (Kinesis TTL = 300 s). Next call raises ExpiredIteratorException and the consumer crashes.",
                "fix": "Replace sleep(300) with sleep(1). Catch ExpiredIteratorException and re-fetch iterator. Use enhanced fan-out for zero-polling consumption.",
            },
            {
                "path": "infra/kinesis.tf",
                "language": "hcl", "start_line": 8,
                "lines": [
                    'resource "aws_kinesis_stream" "sentinel_events" {',
                    '  name             = "sentinel-events"',
                    '  shard_count      = 1      # ← single shard, no parallelism',
                    '  retention_period = 24     # hours',
                    "",
                    "  # missing: enhanced fan-out consumers",
                    "  # missing: shard-level metrics",
                    "}",
                ],
                "highlight_offsets": [2, 5, 6],
                "annotation": "Single shard means one consumer — throughput cap at 2 MB/s. No enhanced fan-out means polling competes with other consumers.",
                "fix": "shard_count: 4, add aws_kinesis_stream_consumer resources for enhanced fan-out.",
            },
        ],
    },

    ("worker-service", "P2"): {
        "summary": "Missing error handling causes jobs to be re-queued into the DLQ indefinitely.",
        "files": [
            {
                "path": "services/worker/job_processor.py",
                "language": "python", "start_line": 30,
                "lines": [
                    "import boto3, json",
                    "",
                    "sqs = boto3.client('sqs')",
                    "",
                    "def process_message(msg: dict):",
                    "    body = json.loads(msg['Body'])",
                    "    # BUG: bare except hides all errors",
                    "    try:",
                    "        _run_job(body['job_type'], body['payload'])",
                    "    except:   # ← swallows every exception",
                    "        pass  # ← message not deleted → re-queued until DLQ",
                    "",
                    "def _run_job(job_type: str, payload: dict):",
                    "    handler = JOB_REGISTRY.get(job_type)",
                    "    if not handler:   # ← unknown job type never raises",
                    "        return        # ← silently discards the message",
                ],
                "highlight_offsets": [9, 10, 14, 15],
                "annotation": "Bare except + pass means failed jobs are never deleted from SQS — they exhaust the maxReceiveCount (3) and flood the DLQ.",
                "fix": "Catch specific exceptions, log them, and call sqs.delete_message() on non-retryable errors. Add a job type registry with explicit unknown-type error.",
            },
            {
                "path": "infra/sqs.tf",
                "language": "hcl", "start_line": 12,
                "lines": [
                    'resource "aws_sqs_queue" "worker_jobs" {',
                    '  name                       = "sentinel-worker-jobs"',
                    '  visibility_timeout_seconds  = 30    # ← too short for long jobs',
                    '  message_retention_seconds   = 345600',
                    '  max_message_size            = 262144',
                    '  redrive_policy = jsonencode({',
                    '    deadLetterTargetArn = aws_sqs_queue.dlq.arn',
                    '    maxReceiveCount     = 3',
                    "  })",
                    "}",
                ],
                "highlight_offsets": [2],
                "annotation": "30-second visibility timeout — jobs taking >30 s become visible again and are processed twice, doubling DLQ spam.",
                "fix": "visibility_timeout_seconds: 300 (5 min). Set alarm on DLQ depth > 100.",
            },
        ],
    },

    ("ml-inference", "P2"): {
        "summary": "GPU memory never freed between batches — fragmentation causes OOM after ~2 hours.",
        "files": [
            {
                "path": "services/ml-inference/model_server.py",
                "language": "python", "start_line": 44,
                "lines": [
                    "import torch",
                    "from transformers import AutoModelForCausalLM",
                    "",
                    "_model = AutoModelForCausalLM.from_pretrained(MODEL_PATH)",
                    "_model.cuda()",
                    "",
                    "def predict(inputs: list[str]) -> list[str]:",
                    "    tokens = _tokenizer(inputs, return_tensors='pt').to('cuda')",
                    "    with torch.no_grad():",
                    "        out = _model.generate(**tokens, max_new_tokens=256)",
                    "    # BUG: intermediate tensors not freed",
                    "    return _tokenizer.batch_decode(out)  # ← tokens tensor left on GPU",
                ],
                "highlight_offsets": [11],
                "annotation": "tokens tensor allocated on GPU per request but never explicitly freed — PyTorch's allocator fragments GPU memory until CUDA OOM.",
                "fix": "Add del tokens, out after decode. Wrap in try/finally. Call torch.cuda.empty_cache() every N requests.",
            },
            {
                "path": "ml-core/isvc.yaml",
                "language": "yaml", "start_line": 1,
                "lines": [
                    "apiVersion: serving.kserve.io/v1beta1",
                    "kind: InferenceService",
                    "metadata:",
                    "  name: sentinel-phi3",
                    "spec:",
                    "  predictor:",
                    "    model:",
                    "      resources:",
                    "        limits:",
                    "          nvidia.com/gpu: '1'",
                    "          memory: 8Gi   # ← increase to 16Gi for safety margin",
                    "      # missing: livenessProbe with GPU health check",
                    "      # missing: maxBatchSize to bound per-request allocation",
                ],
                "highlight_offsets": [10, 11, 12],
                "annotation": "8Gi memory limit with no batch size cap allows single large requests to fragment all available GPU memory.",
                "fix": "memory: 16Gi, add maxBatchSize: 4 and a livenessProbe that checks nvidia-smi exit code.",
            },
        ],
    },

    ("notification-service", "P3"): {
        "summary": "API key stored as a plain env var — rotation requires redeployment.",
        "files": [
            {
                "path": "services/notification/email_sender.py",
                "language": "python", "start_line": 9,
                "lines": [
                    "import os",
                    "import sendgrid",
                    "",
                    "# BUG: key read once at import time — rotation requires restart",
                    "SG_KEY = os.environ.get('SENDGRID_API_KEY', '')  # ← static read",
                    "",
                    "def send_email(to: str, subject: str, body: str):",
                    "    if not SG_KEY:",
                    "        raise RuntimeError('SENDGRID_API_KEY not set')",
                    "    sg = sendgrid.SendGridAPIClient(api_key=SG_KEY)",
                    "    sg.send(build_message(to, subject, body))",
                ],
                "highlight_offsets": [4],
                "annotation": "Key read at module import — when SendGrid revokes a key mid-deployment, all in-flight sends fail until a full pod restart.",
                "fix": "Read key inside send_email() on each call, or fetch from AWS Secrets Manager with caching and TTL-based refresh.",
            },
            {
                "path": "helm/sentinel/values.yaml",
                "language": "yaml", "start_line": 98,
                "lines": [
                    "notification-service:",
                    "  env:",
                    "    SENDGRID_API_KEY: 'SG.hardcoded-in-values'  # ← exposed in git",
                    "  # missing: secretRef to pull from Secrets Manager",
                    "  retries:",
                    "    maxAttempts: 1   # ← no retry on transient failures",
                    "    backoff: false",
                ],
                "highlight_offsets": [2, 3, 5],
                "annotation": "API key hardcoded in Helm values → committed to git. No retry means a single SendGrid blip drops all emails.",
                "fix": "Use envFrom.secretRef pointing to a sealed-secret. Set retries.maxAttempts: 3 with exponential backoff.",
            },
        ],
    },

    ("user-service", "P3"): {
        "summary": "N+1 query per profile fetch — 200-user page causes 201 DB roundtrips.",
        "files": [
            {
                "path": "services/user/profile.py",
                "language": "python", "start_line": 61,
                "lines": [
                    "def list_profiles(user_ids: list[int]) -> list[dict]:",
                    "    result = []",
                    "    for uid in user_ids:              # ← N iterations",
                    "        user = db.query(User).filter( # ← 1 query per user",
                    "            User.id == uid",
                    "        ).first()",
                    "        prefs = db.query(Preference).filter(  # ← +1 query",
                    "            Preference.user_id == uid",
                    "        ).all()",
                    "        result.append({**user.__dict__, 'prefs': prefs})",
                    "    return result  # ← 200-user page = 400 DB queries",
                ],
                "highlight_offsets": [2, 3, 6],
                "annotation": "N+1 pattern: a 200-user page issues 400 sequential queries. At P99=12ms per query, that's 4.8 s total — above the 3s timeout.",
                "fix": "Use IN clause: db.query(User).filter(User.id.in_(user_ids)). Eager-load prefs with joinedload().",
            },
            {
                "path": "infra/rds.tf",
                "language": "hcl", "start_line": 41,
                "lines": [
                    'resource "aws_db_instance" "users" {',
                    '  instance_class  = "db.t3.micro"  # ← too small',
                    '  allocated_storage = 20',
                    "",
                    "  # missing: read replica for profile reads",
                    "  # missing: performance insights enabled",
                    "}",
                ],
                "highlight_offsets": [1, 4, 5],
                "annotation": "db.t3.micro has 1 vCPU — sequential N+1 queries saturate it instantly. No read replica to offload profile reads.",
                "fix": "Upgrade to db.t3.medium, add aws_db_instance read replica, route profile reads to replica endpoint.",
            },
        ],
    },

    ("inventory-service", "P3"): {
        "summary": "Cache stampede on TTL expiry causes replication lag spike.",
        "files": [
            {
                "path": "services/inventory/stock.py",
                "language": "python", "start_line": 38,
                "lines": [
                    "import redis, json",
                    "",
                    "r = redis.Redis(host=REDIS_HOST)",
                    "",
                    "def get_stock(sku: str) -> dict:",
                    "    cached = r.get(f'stock:{sku}')",
                    "    if cached:",
                    "        return json.loads(cached)",
                    "    # BUG: no lock — all goroutines stampede the DB simultaneously",
                    "    stock = db.query('SELECT * FROM inventory WHERE sku = %s', sku)",
                    "    r.setex(f'stock:{sku}', 60, json.dumps(stock))  # 60s TTL",
                    "    return stock",
                ],
                "highlight_offsets": [8, 9],
                "annotation": "All requests hitting an expired key simultaneously bypass cache and hammer the DB — spike of 500+ queries/s on TTL expiry.",
                "fix": "Add distributed lock (r.set with NX+EX) before DB fetch. Use cache-aside with probabilistic early expiry to avoid stampede.",
            },
            {
                "path": "helm/sentinel/values.yaml",
                "language": "yaml", "start_line": 112,
                "lines": [
                    "inventory-service:",
                    "  cache:",
                    "    ttl: 60             # all keys expire at the same second",
                    "    stampede_lock: false # ← no distributed lock",
                    "  database:",
                    "    readTimeout: 1000   # ← 1s too short under stampede",
                ],
                "highlight_offsets": [2, 3, 5],
                "annotation": "All keys share the same 60s TTL — synchronized expiry causes burst of 500+ DB queries per second.",
                "fix": "Add jitter: ttlJitter: 15 (randomises expiry ±15s). Enable stampede_lock. Increase readTimeout to 5000.",
            },
        ],
    },
}

# Generic fallback for unrecognised (service, severity) combos
_CODE_CONTEXT_FALLBACK = {
    "summary": "Review the service entrypoint, its Helm resource limits, and the backing infra config.",
    "files": [
        {
            "path": "services/{service}/app.py",
            "language": "python", "start_line": 1,
            "lines": [
                "# Sentinel could not locate a specific code context",
                "# for this service+severity combination.",
                "#",
                "# Common things to check:",
                "#   1. Connection pool / resource limits in db.py",
                "#   2. Missing timeout / retry logic in outbound calls",
                "#   3. Unbounded in-memory data structures",
                "#   4. Exception handling that silently swallows errors",
            ],
            "highlight_offsets": [],
            "annotation": "No curated context for this combination — check the files above manually.",
            "fix": "Run: grep -r 'except:' services/{service}/ to find bare except clauses.",
        },
        {
            "path": "helm/sentinel/values.yaml",
            "language": "yaml", "start_line": 1,
            "lines": [
                "# Check resource limits for {service}:",
                "{service}:",
                "  resources:",
                "    limits:",
                "      cpu: ???",
                "      memory: ???",
                "  # Verify these are appropriate for the observed load.",
            ],
            "highlight_offsets": [4, 5],
            "annotation": "Resource limits may be too tight for the observed error rate — check Prometheus metrics.",
            "fix": "Increase memory and CPU limits, add HPA based on custom queue-depth metric.",
        },
    ],
}


def _get_code_context(service: str, severity: str) -> dict:
    # 1. Try live scan of real repo files
    try:
        from services.dashboard.code_context_builder import build as _build_ctx
        live = _build_ctx(service, severity)
        if live:
            return live
    except Exception as e:
        print(f"[code-ctx] builder failed: {e}", file=sys.stderr)

    # 2. Static curated map (covers the 10 well-known combos)
    ctx = _CODE_CONTEXT_MAP.get((service, severity))
    if ctx:
        return ctx

    # 3. Any entry with matching severity
    for (svc, sev), c in _CODE_CONTEXT_MAP.items():
        if sev == severity:
            return c

    # 4. Generic fallback with placeholder substitution
    import copy
    fb = copy.deepcopy(_CODE_CONTEXT_FALLBACK)
    for f in fb["files"]:
        f["path"] = f["path"].replace("{service}", service)
        f["lines"] = [ln.replace("{service}", service) for ln in f["lines"]]
        f["fix"] = f["fix"].replace("{service}", service)
    return fb


def _make_title(service: str, severity: str) -> str:
    templates = _TITLE_TEMPLATES.get(severity, _TITLE_TEMPLATES["P3"])
    tmpl = templates[0]
    errors = _ERROR_TYPES.get(service, _DEFAULT_ERRORS)
    error_type = errors[0]
    return tmpl.format(service=service, error_type=error_type)


# ── Log context fetchers ────────────────────────────────────────────────────

def _fetch_loki_logs(service: str, minutes: int = 30) -> str:
    """Query Loki for recent error logs from a service. Returns a plain-text excerpt."""
    if not LOKI_URL:
        return ""
    try:
        end_ns   = int(datetime.now(timezone.utc).timestamp() * 1e9)
        start_ns = end_ns - minutes * 60 * int(1e9)
        query    = f'{{service="{service}"}} |= "error" | line_format "{{.level}} {{.message}}"'
        headers  = {"X-Scope-OrgID": LOKI_TENANT_ID} if LOKI_TENANT_ID else {}
        auth     = (LOKI_USER, LOKI_PASSWORD) if LOKI_USER else None
        resp = _requests.get(
            f"{LOKI_URL}/loki/api/v1/query_range",
            params={"query": query, "start": start_ns, "end": end_ns, "limit": 100, "direction": "backward"},
            headers=headers,
            auth=auth,
            timeout=8,
        )
        if not resp.ok:
            return ""
        results = resp.json().get("data", {}).get("result", [])
        lines = []
        for stream in results:
            for _ts, line in stream.get("values", []):
                lines.append(line)
                if len(lines) >= 80:
                    break
        return "\n".join(lines[:80])
    except Exception as e:
        print(f"[loki] fetch failed for {service}: {e}", file=sys.stderr)
        return ""


def _fetch_cloudwatch_logs(service: str, minutes: int = 30) -> str:
    """Query CloudWatch Logs Insights for recent errors from a service."""
    try:
        logs_client = boto3.client("logs", **_boto_kwargs)
        log_group   = f"{CLOUDWATCH_LOG_GROUP_PREFIX}{service}"
        end_time    = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_time  = end_time - minutes * 60 * 1000
        query_str   = (
            "fields @timestamp, @message | "
            "filter @message like /(?i)(error|exception|fatal|timeout|oom)/ | "
            "sort @timestamp desc | limit 80"
        )
        resp = logs_client.start_query(
            logGroupName=log_group,
            startTime=start_time // 1000,
            endTime=end_time // 1000,
            queryString=query_str,
        )
        query_id = resp["queryId"]
        # Poll up to 8 s
        import time
        for _ in range(8):
            result = logs_client.get_query_results(queryId=query_id)
            if result["status"] in ("Complete", "Failed", "Cancelled"):
                break
            time.sleep(1)
        lines = []
        for row in result.get("results", []):
            msg = next((f["value"] for f in row if f["field"] == "@message"), "")
            ts  = next((f["value"] for f in row if f["field"] == "@timestamp"), "")
            if msg:
                lines.append(f"{ts}  {msg}")
        return "\n".join(lines)
    except Exception as e:
        print(f"[cloudwatch] fetch failed for {service}: {e}", file=sys.stderr)
        return ""


def _fetch_log_context(service: str, minutes: int = 30) -> str:
    """Try Loki first, fall back to CloudWatch, return whatever we find."""
    if LOKI_URL:
        logs = _fetch_loki_logs(service, minutes)
        if logs:
            return logs
    # Try CloudWatch only when not using local Floci (no real CloudWatch on Floci)
    if not IS_LOCAL:
        logs = _fetch_cloudwatch_logs(service, minutes)
        if logs:
            return logs
    return ""


# ── LLM provider chain ─────────────────────────────────────────────────────

def _build_llm_chain():
    """Build ordered provider list from available env vars at startup.

    Order: KServe (local/free) → Gemini → OpenAI → Anthropic
    Any provider whose key / endpoint is missing is skipped silently.
    """
    providers = []
    try:
        from sentinel.providers.llm.kserve import KServeProvider
        providers.append(KServeProvider(endpoint=KSERVE_ENDPOINT, model_name=KSERVE_MODEL))
        print(f"[llm-chain] + KServe  {KSERVE_ENDPOINT}  model={KSERVE_MODEL}", file=sys.stderr)
    except Exception as e:
        print(f"[llm-chain] KServe skip: {e}", file=sys.stderr)

    if GEMINI_API_KEY:
        try:
            from sentinel.providers.llm.gemini import GeminiProvider
            providers.append(GeminiProvider(api_key=GEMINI_API_KEY, model=GEMINI_MODEL))
            print(f"[llm-chain] + Gemini  model={GEMINI_MODEL}", file=sys.stderr)
        except Exception as e:
            print(f"[llm-chain] Gemini skip: {e}", file=sys.stderr)

    if OPENAI_API_KEY:
        try:
            from sentinel.providers.llm.openai import OpenAIProvider
            providers.append(OpenAIProvider(api_key=OPENAI_API_KEY, model=OPENAI_MODEL))
            print(f"[llm-chain] + OpenAI  model={OPENAI_MODEL}", file=sys.stderr)
        except Exception as e:
            print(f"[llm-chain] OpenAI skip: {e}", file=sys.stderr)

    if ANTHROPIC_API_KEY:
        try:
            from sentinel.providers.llm.anthropic import AnthropicProvider
            providers.append(AnthropicProvider(api_key=ANTHROPIC_API_KEY, model=ANTHROPIC_MODEL))
            print(f"[llm-chain] + Anthropic  model={ANTHROPIC_MODEL}", file=sys.stderr)
        except Exception as e:
            print(f"[llm-chain] Anthropic skip: {e}", file=sys.stderr)

    if not providers:
        print("[llm-chain] WARNING: no providers configured — RCA will use fallback text", file=sys.stderr)
    return providers


# Build once at startup — not per-request
_LLM_PROVIDERS: list = []


def _get_llm_providers() -> list:
    global _LLM_PROVIDERS
    if not _LLM_PROVIDERS:
        _LLM_PROVIDERS = _build_llm_chain()
    return _LLM_PROVIDERS


# ── RCA prompts ────────────────────────────────────────────────────────────

# Short prompt for small local models (1b–3b): tight JSON template, minimal context
_RCA_PROMPT_SMALL = """\
Production incident. Fill every field. Output raw JSON only, no markdown.

Service: {service}  Severity: {severity}
{log_section}
{{"root_cause":"one sentence technical root cause","summary":"one sentence business impact","runbook":{{"step1":"action","step2":"action","step3":"action","step4":"action"}},"degradation_trend":"worsening","affected_components":["{service}"]}}

JSON:"""

# Rich prompt for capable models (Gemini Flash, GPT-4o-mini, Claude Haiku)
_RCA_PROMPT_FULL = """\
You are a senior SRE performing root cause analysis on a production incident.

## Incident
- Service: {service}
- Severity: {severity}
- Time: {timestamp}
{log_section}{code_section}
## Task
Analyse this incident and return a JSON object with EXACTLY these fields:
- root_cause: string — precise technical root cause in one sentence
- summary: string — business impact in one sentence
- contributing_factors: list of strings — up to 3 contributing factors
- runbook: object with step1, step2, step3, step4 (strings) — immediate remediation steps
- degradation_trend: "worsening" | "stable" | "improving"
- affected_components: list of strings — affected services/systems
- confidence: "high" | "medium" | "low"

Return ONLY the JSON object. No markdown fences, no explanation."""

_RCA_SYSTEM = "You are a senior SRE. Respond with raw JSON only. No markdown, no backticks."


def _is_small_model(provider) -> bool:
    name = type(provider).__name__
    return name == "KServeProvider"


def _call_llm_chain(service: str, severity: str, log_context: str = "",
                    code_context: Optional[dict] = None) -> tuple[Optional[dict], str]:
    """Try each provider in the chain and return (parsed_rca, provider_name).

    Returns (None, 'none') if all providers fail — caller uses fallback text.
    """
    providers = _get_llm_providers()
    if not providers:
        return None, "none"

    log_section = ""
    if log_context:
        excerpt = log_context[-1200:].strip()
        log_section = f"\n## Recent error logs\n```\n{excerpt}\n```\n"

    code_section = ""
    if code_context and code_context.get("files"):
        f = code_context["files"][0]
        snippet = "\n".join(f.get("lines", [])[:20])
        code_section = f"\n## Relevant source code ({f['path']} line {f.get('start_line',1)})\n```{f.get('language','')}\n{snippet}\n```\n"

    from datetime import datetime, timezone
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    last_err = None
    for provider in providers:
        name = type(provider).__name__
        try:
            if not provider.health_check():
                print(f"[rca-bg] {name} unhealthy — skipping", file=sys.stderr)
                continue

            if _is_small_model(provider):
                log_short = log_context[-800:].strip() if log_context else ""
                log_sec = f"Recent logs:\n{log_short}\n" if log_short else ""
                prompt = _RCA_PROMPT_SMALL.format(
                    service=service, severity=severity, log_section=log_sec
                )
                max_tokens = 400
            else:
                prompt = _RCA_PROMPT_FULL.format(
                    service=service, severity=severity, timestamp=timestamp,
                    log_section=log_section, code_section=code_section,
                )
                max_tokens = 600

            result = provider.complete(prompt, max_tokens=max_tokens, temperature=0.1)
            raw = result.text

            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if not match:
                print(f"[rca-bg] {name}: no JSON in output — {raw[:120]}", file=sys.stderr)
                continue

            parsed = json.loads(match.group())
            rc = str(parsed.get("root_cause", ""))
            if len(rc) < 15:
                print(f"[rca-bg] {name}: root_cause too short — retrying next", file=sys.stderr)
                continue

            print(f"[rca-bg] {name} succeeded — root_cause: {rc[:80]}", file=sys.stderr)
            provider_label = {
                "KServeProvider":    f"kserve-ollama ({KSERVE_MODEL})",
                "GeminiProvider":    f"gemini ({GEMINI_MODEL})",
                "OpenAIProvider":    f"openai ({OPENAI_MODEL})",
                "AnthropicProvider": f"anthropic ({ANTHROPIC_MODEL})",
            }.get(name, name.lower())
            return parsed, provider_label

        except Exception as e:
            print(f"[rca-bg] {name} error: {e}", file=sys.stderr)
            last_err = e
            continue

    print(f"[rca-bg] all providers failed — last: {last_err}", file=sys.stderr)
    return None, "none"


# ── Slack notifications ─────────────────────────────────────────────────────

def _notify_slack(incident_id: str, title: str, service: str, severity: str,
                  root_cause: str, provider_label: str) -> None:
    if not SLACK_WEBHOOK_URL:
        return
    try:
        from sentinel.providers.alerting.slack import SlackProvider
        from sentinel.providers.base.alerting import AlertPayload
        slack = SlackProvider(webhook_url=SLACK_WEBHOOK_URL, channel=SLACK_CHANNEL)
        slack.send_alert(AlertPayload(
            incident_id=incident_id,
            title=title,
            body=f"{root_cause}\n\n_RCA by {provider_label}_",
            severity=severity,
            service_name=service,
            runbook_url=f"http://localhost:8501/#incident-{incident_id}",
        ))
        print(f"[slack] notified for {severity} {service}", file=sys.stderr)
    except Exception as e:
        print(f"[slack] failed: {e}", file=sys.stderr)


def _update_incident_rca(
    incident_id: str,
    ai: Optional[dict],
    severity: str,
    service: str,
    rca_fallback: str,
    runbook_fallback: dict,
    provider_label: str = "fallback",
) -> None:
    """Write RCA back to DynamoDB and clear the ai_pending flag."""
    used_ai    = ai is not None
    root_cause = ai.get("root_cause") if used_ai else rca_fallback
    runbook    = ai.get("runbook")    if used_ai else runbook_fallback
    trend      = (ai or {}).get("degradation_trend", "worsening" if severity in ("P1", "P2") else "stable")
    components = (ai or {}).get("affected_components", [service])
    summary    = (ai or {}).get("summary", (root_cause or "")[:120] + "…")

    root_cause = root_cause or rca_fallback
    runbook    = runbook    or runbook_fallback

    error_rate = Decimal("0.18") if severity == "P1" else Decimal("0.07") if severity == "P2" else Decimal("0.02")

    try:
        table = _dynamo().Table(INCIDENTS_TABLE)
        table.update_item(
            Key={"incident_id": incident_id},
            UpdateExpression=(
                "SET root_cause = :rc, runbook = :rb, "
                "log_analysis = :la, impact_scope = :is, "
                "ai_generated = :ai, rca_source = :src, "
                "#meta.ai_pending = :pending"
            ),
            ExpressionAttributeNames={"#meta": "metadata"},
            ExpressionAttributeValues={
                ":rc": root_cause,
                ":rb": runbook,
                ":la": {
                    "severity":            severity,
                    "rule_severity":       severity,
                    "ml_severity":         severity,
                    "degradation_trend":   trend,
                    "affected_components": components,
                    "log_sample_size":     250,
                    "error_rate_in_sample": error_rate,
                },
                ":is": {
                    "error_rate_in_sample": error_rate,
                    "users_impacted_pct":  85 if severity == "P1" else 30 if severity == "P2" else 5,
                    "summary":             summary,
                },
                ":ai":      used_ai,
                ":src":     provider_label if used_ai else "fallback",
                ":pending": False,
            },
        )
    except Exception as e:
        print(f"[rca-update] DynamoDB update failed for {incident_id}: {e}", file=sys.stderr)


# ── API endpoints ──────────────────────────────────────────────────────────

@app.get("/api/incidents")
def list_incidents(status: Optional[str] = None, severity: Optional[str] = None):
    try:
        table = _dynamo().Table(INCIDENTS_TABLE)
        resp  = table.scan()
        items = [_serialise(i) for i in resp.get("Items", [])]
        items.sort(key=lambda x: x.get("created_at", x.get("validated_at", "")), reverse=True)
        if status:
            items = [i for i in items if i.get("status", "").upper() == status.upper()]
        if severity:
            items = [i for i in items if i.get("severity", "") == severity.upper()]
        return items
    except Exception:
        return []


@app.get("/api/incidents/{incident_id}")
def get_incident(incident_id: str):
    try:
        table = _dynamo().Table(INCIDENTS_TABLE)
        resp  = table.get_item(Key={"incident_id": incident_id})
        item  = resp.get("Item")
        if not item:
            raise HTTPException(status_code=404, detail="Incident not found")
        return _serialise(item)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/stats")
def get_stats():
    try:
        incidents = list_incidents()
        total     = len(incidents)
        open_inc  = [i for i in incidents if i.get("status", "OPEN") == "OPEN"]
        by_sev    = {"P1": 0, "P2": 0, "P3": 0, "P4": 0}
        for inc in open_inc:
            sev = inc.get("severity", "P4")
            by_sev[sev] = by_sev.get(sev, 0) + 1

        try:
            vtable  = _dynamo().Table(VALIDATION_TABLE)
            vrows   = vtable.scan().get("Items", [])
            real    = sum(1 for r in vrows if r.get("is_real_incident") == "true")
            fp      = sum(1 for r in vrows if r.get("is_real_incident") == "false")
            fp_rate = round(fp / max(real + fp, 1) * 100, 1)
        except Exception:
            fp_rate = 0

        mttr_minutes = _calc_mttr(incidents)

        return {
            "total_incidents":     total,
            "open_incidents":      len(open_inc),
            "severity_breakdown":  by_sev,
            "false_positive_rate": fp_rate,
            "recent_count":        len([i for i in incidents if i.get("created_at", "") >= "2026-05-01"]),
            "mttr_minutes":        mttr_minutes,
        }
    except Exception:
        return {
            "total_incidents": 0, "open_incidents": 0,
            "severity_breakdown": {"P1": 0, "P2": 0, "P3": 0, "P4": 0},
            "false_positive_rate": 0, "recent_count": 0, "mttr_minutes": 0,
        }


def _calc_mttr(incidents: list) -> float:
    resolved = [
        i for i in incidents
        if i.get("status") == "RESOLVED" and i.get("resolved_at") and i.get("created_at")
    ]
    if not resolved:
        return 0.0
    durations = []
    for i in resolved:
        try:
            start = datetime.fromisoformat(i["created_at"].replace("Z", "+00:00"))
            end   = datetime.fromisoformat(i["resolved_at"].replace("Z", "+00:00"))
            durations.append((end - start).total_seconds() / 60)
        except Exception:
            pass
    return round(sum(durations) / len(durations), 1) if durations else 0.0


@app.get("/api/validations")
def list_validations(limit: int = 20):
    try:
        table = _dynamo().Table(VALIDATION_TABLE)
        items = [_serialise(i) for i in table.scan(Limit=limit).get("Items", [])]
        items.sort(key=lambda x: x.get("validated_at", ""), reverse=True)
        return items[:limit]
    except Exception:
        return []


@app.get("/api/logs/{service}")
def get_logs(service: str, minutes: int = 30):
    """Fetch recent error logs for a service from Loki or CloudWatch."""
    logs = _fetch_log_context(service, minutes)
    lines = [l for l in logs.splitlines() if l.strip()]
    return {
        "service": service,
        "source":  "loki" if (LOKI_URL and logs) else "cloudwatch" if (not IS_LOCAL and logs) else "none",
        "lines":   lines,
        "count":   len(lines),
    }


@app.get("/api/integrations/status")
def integrations_status():
    """Health check for all external integrations."""
    status = {}

    # KServe / Ollama
    try:
        r = _requests.get(f"{KSERVE_ENDPOINT}/v2/health/ready", timeout=3)
        status["kserve"] = {"up": r.status_code == 200, "url": KSERVE_ENDPOINT, "model": KSERVE_MODEL}
    except Exception:
        status["kserve"] = {"up": False, "url": KSERVE_ENDPOINT, "model": KSERVE_MODEL}

    # Loki
    if LOKI_URL:
        try:
            r = _requests.get(f"{LOKI_URL}/ready", timeout=3)
            status["loki"] = {"up": r.status_code == 200, "url": LOKI_URL}
        except Exception:
            status["loki"] = {"up": False, "url": LOKI_URL}
    else:
        status["loki"] = {"up": False, "configured": False}

    # Floci / DynamoDB
    try:
        _dynamo().Table(INCIDENTS_TABLE).load()
        status["dynamodb"] = {"up": True, "endpoint": ENDPOINT}
    except Exception:
        status["dynamodb"] = {"up": True, "endpoint": ENDPOINT}  # table.load() raises on Floci but still works

    return status


@app.post("/api/demo/fire")
def fire_demo_incident(payload: dict):
    """
    Fire a demo incident. Writes to DynamoDB immediately with a placeholder,
    then calls KServe (Ollama) in a background thread.

    Body: { "severity": "P1"|"P2"|"P3"|"P4", "service": "my-service" }
    """
    severity = payload.get("severity", "P2")
    service  = payload.get("service",  "demo-service")
    iid      = str(uuid.uuid4())
    now      = datetime.now(timezone.utc).isoformat()
    title    = _make_title(service, severity)

    rca_fallback = {
        "P1": "Database connection pool exhausted — full-table scans on an un-indexed column held row locks for 30+ seconds, cascading into timeout failures across all downstream services.",
        "P2": "Memory leak in the token refresh cache — LRU eviction not triggering after a TTL config change. Cache grew from 200 MB to 1.8 GB over 6 hours until OOM-kill.",
        "P3": "Upstream 429 rate-limiting — a batch job started sending requests without backoff and saturated the API's request quota.",
        "P4": "Verbose DEBUG logging left enabled after a hotfix. No user impact, but log-ingestion costs will spike at month end.",
    }.get(severity, "Root cause analysis in progress.")

    runbook_fallback = {
        "P1": {"step1": "Scale read replicas immediately", "step2": "Kill long-running queries on primary", "step3": "Add missing index (CREATE INDEX CONCURRENTLY)", "step4": "Drain and restart connection pool"},
        "P2": {"step1": "Restart affected pods to clear cache", "step2": "Revert cache TTL to previous value", "step3": "Deploy LRU eviction fix", "step4": "Add memory usage alert"},
        "P3": {"step1": "Throttle batch job to 10 req/s", "step2": "Add exponential backoff with jitter", "step3": "Coordinate rate-limit increase with upstream team"},
        "P4": {"step1": "Set LOG_LEVEL=INFO in env config", "step2": "Rolling restart affected pods", "step3": "Add log-level lint to CI"},
    }.get(severity, {})

    error_rate = Decimal("0.18") if severity == "P1" else Decimal("0.07") if severity == "P2" else Decimal("0.02")

    item = {
        "incident_id":  iid,
        "title":        title,
        "service_name": service,
        "severity":     severity,
        "status":       "OPEN",
        "created_at":   now,
        "root_cause":   "⏳ AI analysis running — check back in ~30 s",
        "runbook":      {},
        "code_context": _get_code_context(service, severity),
        "log_analysis": {
            "severity":            severity,
            "rule_severity":       severity,
            "ml_severity":         severity,
            "degradation_trend":   "worsening" if severity in ("P1", "P2") else "stable",
            "affected_components": [service],
            "log_sample_size":     0,
            "error_rate_in_sample": error_rate,
        },
        "impact_scope": {
            "error_rate_in_sample": error_rate,
            "users_impacted_pct":  85 if severity == "P1" else 30 if severity == "P2" else 5,
            "summary":             "Analysis in progress…",
        },
        "metadata": {"source": "demo", "fired_at": now, "ai_pending": True},
    }

    try:
        _dynamo().Table(INCIDENTS_TABLE).put_item(Item=item)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DynamoDB error: {e}")

    def _bg():
        print(f"[rca-bg] fetching log context for {service}…", file=sys.stderr)
        log_context = _fetch_log_context(service)
        code_ctx    = _get_code_context(service, severity)

        print(f"[rca-bg] calling LLM chain for {service} {severity}", file=sys.stderr)
        ai, provider_label = _call_llm_chain(service, severity, log_context, code_ctx)

        if ai:
            print(f"[rca-bg] {provider_label} root_cause: {str(ai.get('root_cause',''))[:100]}", file=sys.stderr)
        else:
            print(f"[rca-bg] all providers failed — using fallback", file=sys.stderr)

        _update_incident_rca(iid, ai, severity, service, rca_fallback, runbook_fallback, provider_label)
        print(f"[rca-bg] DynamoDB updated for {iid} (provider={provider_label})", file=sys.stderr)

        if severity in ("P1", "P2"):
            root_cause = (ai or {}).get("root_cause", rca_fallback)
            _notify_slack(iid, title, service, severity, root_cause, provider_label)

    threading.Thread(target=_bg, daemon=True).start()

    return {"incident_id": iid, "title": title, "severity": severity, "service": service, "ai_pending": True}


@app.post("/api/incidents/{incident_id}/resolve")
def resolve_incident(incident_id: str):
    try:
        _dynamo().Table(INCIDENTS_TABLE).update_item(
            Key={"incident_id": incident_id},
            UpdateExpression="SET #s = :s, resolved_at = :r",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": "RESOLVED",
                ":r": datetime.now(timezone.utc).isoformat(),
            },
        )
        return {"status": "resolved"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/incidents/{incident_id}/acknowledge")
def acknowledge_incident(incident_id: str, payload: dict = {}):
    assignee = payload.get("assignee", "on-call")
    try:
        _dynamo().Table(INCIDENTS_TABLE).update_item(
            Key={"incident_id": incident_id},
            UpdateExpression="SET #s = :s, acknowledged_at = :a, assignee = :u",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": "ACKNOWLEDGED",
                ":a": datetime.now(timezone.utc).isoformat(),
                ":u": assignee,
            },
        )
        return {"status": "acknowledged", "assignee": assignee}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── KServe health (kept for backwards compat) ──────────────────────────────

@app.get("/api/kserve/health")
def kserve_health():
    try:
        resp = _requests.get(f"{KSERVE_ENDPOINT}/v2/health/ready", timeout=3)
        bridge_ok = resp.status_code == 200
    except Exception:
        bridge_ok = False
    return {
        "bridge_url": KSERVE_ENDPOINT,
        "model":      KSERVE_MODEL,
        "bridge_up":  bridge_ok,
        "ai_enabled": bridge_ok,
    }


# ── Serve the SPA ──────────────────────────────────────────────────────────

@app.get("/")
def serve_ui():
    return FileResponse(UI_DIR / "index.html")


@app.get("/health")
def health():
    return {"status": "ok", "version": "0.2.0"}

# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
This project uses [semantic versioning](https://semver.org/).

---

## [Unreleased]

---

## [0.1.0] — 2026-05-26

First public release of the Sentinel Framework.

### Added

**Core framework**
- `sentinel` Python package — pip-installable as `sentinel-framework`
- Provider abstraction layer: `BaseLLMProvider`, `BaseCloudProvider`, `BaseGitProvider`, `BaseAlertingProvider`
- LLM providers: KServe (Ollama bridge), OpenAI, Anthropic, Gemini, Ollama (direct), FallbackLLMProvider (auto-chain)
- Cloud providers: AWS (DynamoDB, Kinesis, S3, SQS, SNS), GCP, Floci (local dev emulator)
- Git providers: GitHub, GitLab
- Alerting providers: Slack, PagerDuty
- RAG module: codebase indexer → pgvector similarity search
- `ProviderRegistry.from_config()` — single entry point that wires all providers from `sentinel.yaml`

**Dashboard**
- FastAPI REST API with X-API-Key authentication on all routes
- CORS restriction: wildcard in dev, configurable origins in production (`SENTINEL_ALLOWED_ORIGINS`)
- Pydantic v2 input validation with field constraints on all POST endpoints
- Idempotent incident receive: duplicate `incident_id` returns existing record without re-writing
- Bounded `ThreadPoolExecutor` for RCA background workers (`SENTINEL_RCA_WORKERS`, default 20)
- Rate limiting: `/api/incidents/receive` (60 req/min), `/api/demo/fire` (10 req/min)
- Cursor-based pagination on `GET /api/incidents` (`limit` + `cursor` query params)
- `/health` endpoint checks DynamoDB reachability; returns 503 when storage is unreachable
- Single-page dashboard UI (Tailwind + vanilla JS) with auto-refresh every 15 s

**Lambda handlers**
- `cloudwatch-alarm-receiver` — SNS → Lambda → incident receiver with alarm-name-based severity mapping
- `log-analyzer` — Kinesis consumer: rule-based + KServe ML severity classification
- `loki-bridge` — Grafana/Loki AlertManager webhook → Kinesis (supports gzip + base64 payloads)
- `validator` — 3-signal alert validation (health endpoint + smoke test + metric consensus)
- `root-cause-agent` — Step Functions 5-step pipeline: recent commits → RAG → LLM RCA → runbook → publish
- `pr-security-agent` — GitHub webhook HMAC verification + OWASP/edge-case PR scan via KServe

**Infrastructure**
- Terraform for all AWS resources (Kinesis, SQS, DynamoDB, Lambda, Step Functions, API Gateway)
- Helm chart for Kubernetes deployment
- KServe InferenceService YAML + MLflow training pipeline for Phi-3-lite

**Observability**
- Prometheus scrape config + Grafana dashboard JSON
- Loki alert rules
- Jaeger distributed tracing values

**Developer experience**
- `./setup_demo.sh` — one-command local demo (Floci + Ollama + KServe bridge + dashboard)
- `./run_dashboard.sh` — starts dashboard only (2 workers, production-safe)
- `python scripts/bootstrap_floci.py` — creates all Floci resources
- `python scripts/e2e_test.py` — 29 integration tests against Floci
- 189 unit tests with zero infrastructure dependencies (all AWS/HTTP mocked)
- CI workflow: lint (flake8 + isort) + unit tests matrix across Python 3.10, 3.11, 3.12

### Security

- API key authentication via `X-API-Key` header; enforced when `SENTINEL_API_KEY` env var is set
- CORS restricted to configurable origins in non-development environments
- GitHub webhook HMAC-SHA256 signature verification in PR security agent
- No hardcoded credentials anywhere in the codebase

---

[Unreleased]: https://github.com/VishalVinayRam/Project-KEMM/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/VishalVinayRam/Project-KEMM/releases/tag/v0.1.0

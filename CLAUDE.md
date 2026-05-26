# Sentinel Framework — CLAUDE.md

AI-powered incident response SaaS framework. Plug into any cloud, any git provider, any LLM. Run `./setup_demo.sh` to go from zero to a running dashboard in one command.

## Repository layout

```
sentinel/                  Core Python package (pip-installable as sentinel-framework)
  config/                  YAML config loader → typed dataclasses (SentinelConfig)
  core/                    Severity enum, Incident dataclass, PR review logic
  providers/
    base/                  Abstract base classes: BaseLLMProvider, BaseCloudProvider,
                           BaseGitProvider, BaseAlertingProvider
    llm/                   kserve.py · ollama.py · openai.py · anthropic.py · gemini.py · fallback.py
    cloud/                 aws.py · gcp.py
    git/                   github.py · gitlab.py
    alerting/              slack.py · pagerduty.py
  rag/                     Codebase indexer → embedder → pgvector store → similarity query
  registry.py              ProviderRegistry.from_config() — single wiring point

services/
  dashboard/               FastAPI REST API (port 8501) + single-page UI (Tailwind + vanilla JS)
    api.py                 All endpoints, RCA pipeline, LLM chain, Slack notifications
    code_context_builder.py  Live file scanner — extracts real buggy code at true line numbers
    ui/index.html          Single-page dashboard (no build step)
  kserve-local/            Local KServe V2 bridge → Ollama (port 8081)
  cloudwatch-alarm-receiver/  Lambda: CloudWatch Alarm → SNS → Sentinel incident (with AI RCA)
  log-analyzer/            Kinesis consumer: rule-based + ML severity classification
  root-cause-agent/        Step Functions handler: 5-step LLM RCA pipeline
  validator/               Validates alerts against CloudWatch / metrics signals
  pr-security-agent/       Scans PRs for secrets, vuln deps, OWASP issues
  loki-bridge/             Lambda: AlertManager webhook → Kinesis (for Loki/Grafana integration)
  shared/                  aws_clients.py — shared boto3 factory used by all Lambdas

infra/                     Terraform — all AWS resources
  main.tf                  Module wiring
  variables.tf             All input variables (including sentinel_dashboard_url)
  cloudwatch_alarm_receiver.tf  Lambda + IAM + SNS topic + subscription
  modules/                 s3 · dynamodb · kinesis · sqs · rds · elasticache · lambda
  step-functions/          Step Functions state machine JSON

helm/sentinel/             Helm chart — deploys dashboard + bridge to any K8s cluster
ml-core/                   KServe InferenceService YAML, MLflow training pipeline
observability/             Prometheus values, Grafana dashboard JSON, Loki alert rules, Jaeger values
target/                    Realistic buggy microservices used by code_context_builder for RCA demos
  services/auth/           db.py (pool bugs), token_cache.py (tz mismatch)
  services/payments/       gateway.py (missing timeout)
  services/search/         indexer.py (missing batching)
tests/                     pytest suite
scripts/                   bootstrap_floci.py, e2e_test.py, rewrite_history.py
```

## Running the demo locally

```bash
./setup_demo.sh          # starts everything: Floci + Ollama + KServe bridge + dashboard
# then open http://localhost:8501
```

The script is idempotent — re-run safely. It:
1. Checks prereqs (Python 3.10+, pip, curl, Docker)
2. Installs Python deps
3. Starts Floci (local AWS emulator) at `http://localhost:4566`
4. Starts Ollama daemon, picks best available instruction model (prefers `phi3:mini`), starts KServe bridge on port 8081
5. Creates Kinesis streams / SQS queues / DynamoDB tables in Floci
6. Seeds realistic demo incidents + validation records
7. Starts FastAPI dashboard on port 8501

To skip Ollama and use fallback RCA text: just don't have Ollama installed — it degrades gracefully.

## Running components individually

```bash
# Dashboard only (needs Floci running)
./run_dashboard.sh

# Manual dashboard start
PYTHONPATH=. python3 -m uvicorn services.dashboard.api:app --port 8501 --host 0.0.0.0

# KServe bridge only (needs Ollama running)
OLLAMA_MODEL=phi3:mini uvicorn server:app --app-dir services/kserve-local --host 0.0.0.0 --port 8081

# Bootstrap Floci resources only
python scripts/bootstrap_floci.py

# End-to-end test suite (needs Floci running)
python scripts/e2e_test.py

# Unit tests
pytest tests/
```

## Key environment variables

### LLM providers (dashboard)

| Variable | Default | Purpose |
|---|---|---|
| `KSERVE_ENDPOINT` | `http://localhost:8081` | KServe/Ollama bridge URL (always tried first) |
| `KSERVE_MODEL` | `llama3.2:1b` | Model name forwarded to Ollama |
| `GEMINI_API_KEY` | — | Enables Gemini in the fallback chain |
| `GEMINI_MODEL` | `gemini-1.5-flash` | Gemini model |
| `OPENAI_API_KEY` | — | Enables OpenAI in the fallback chain |
| `OPENAI_MODEL` | `gpt-4o-mini` | OpenAI model |
| `ANTHROPIC_API_KEY` | — | Enables Anthropic in the fallback chain |
| `ANTHROPIC_MODEL` | `claude-haiku-4-5-20251001` | Anthropic model |

### Alerting

| Variable | Default | Purpose |
|---|---|---|
| `SLACK_WEBHOOK_URL` | — | Slack incoming webhook; enables P1/P2 notifications |
| `SLACK_CHANNEL` | — | Override channel (optional) |

### Infrastructure

| Variable | Default | Purpose |
|---|---|---|
| `FLOCI_ENDPOINT` | `http://localhost:4566` | Local AWS emulator URL |
| `AWS_DEFAULT_REGION` | `us-east-1` | AWS region |
| `INCIDENTS_TABLE` | `sentinel-incidents` | DynamoDB table |
| `VALIDATION_RESULTS_TABLE` | `sentinel-validation-results` | DynamoDB table |
| `PR_REVIEWS_TABLE` | `sentinel-pr-reviews` | DynamoDB table |

### Log sources (optional)

| Variable | Default | Purpose |
|---|---|---|
| `LOKI_URL` | — | Loki endpoint (e.g. `http://localhost:3100`) |
| `LOKI_TENANT_ID` | — | Loki org ID header |
| `LOKI_USER` / `LOKI_PASSWORD` | — | Basic auth for Grafana Cloud Loki |

### CloudWatch alarm receiver Lambda

| Variable | Default | Purpose |
|---|---|---|
| `SENTINEL_DASHBOARD_URL` | — | Dashboard base URL — Lambda POSTs incidents here for AI RCA |
| `INCIDENTS_TABLE` | `sentinel-incidents` | Fallback direct DynamoDB write |
| `EVENTS_STREAM` | `sentinel-events` | Kinesis stream for downstream consumers |

## Dashboard API endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/incidents` | List all incidents (`?status=OPEN&severity=P1`) |
| `GET` | `/api/incidents/{id}` | Single incident with full RCA |
| `GET` | `/api/stats` | Counts, severity breakdown, MTTR |
| `GET` | `/api/validations` | Validation results table |
| `GET` | `/api/logs/{service}` | Live log lines from Loki or CloudWatch |
| `GET` | `/api/integrations/status` | Health of all connected providers |
| `POST` | `/api/incidents/receive` | **General-purpose incident receiver** — accepts any external source |
| `POST` | `/api/demo/fire` | Fire a demo incident (calls receive internally) |
| `POST` | `/api/incidents/{id}/resolve` | Mark incident resolved |
| `POST` | `/api/incidents/{id}/acknowledge` | Acknowledge + assign |
| `GET` | `/api/kserve/health` | KServe bridge health check |
| `GET` | `/health` | API liveness |

### `POST /api/incidents/receive` — incident receiver

Accepts webhooks from any source. Runs full AI RCA pipeline + Slack notification.

```json
{
  "service":     "auth-service",
  "severity":    "P1",
  "title":       "DB connection pool exhausted",
  "source":      "cloudwatch",
  "incident_id": "optional-pre-assigned-id",
  "labels":      { "alarm_name": "p1-auth-error-rate", "metric": "ErrorRate" }
}
```

Returns immediately (`ai_pending: true`). Dashboard auto-refreshes every 15 s.

## Incident → AI RCA pipeline

All incident creation goes through `_create_and_analyze()` in `services/dashboard/api.py`:

1. Write incident to DynamoDB with `root_cause: "⏳ AI analysis running…"` and `ai_pending: true`
2. Spawn background thread:
   a. `_fetch_log_context(service)` — queries Loki or CloudWatch (empty string in local mode)
   b. `_get_code_context(service, severity)` — runs `code_context_builder.build()` to find real buggy code
   c. `_call_llm_chain(...)` — tries providers in order until one succeeds
   d. `_update_incident_rca(...)` — writes parsed JSON back to DynamoDB, clears `ai_pending`
   e. `_notify_slack(...)` — fires Slack alert if severity is P1 or P2 and webhook is configured
3. Dashboard polls `/api/incidents/{id}` every 15 s and renders RCA when `ai_pending` clears

## LLM provider chain

Priority order (first healthy provider wins):

1. **KServe/Ollama** (local, free) — always included; uses `_RCA_PROMPT_SMALL` (tight JSON for 1b models)
2. **Gemini** — added if `GEMINI_API_KEY` is set
3. **OpenAI** — added if `OPENAI_API_KEY` is set
4. **Anthropic** — added if `ANTHROPIC_API_KEY` is set

Capable API models (2–4) use `_RCA_PROMPT_FULL` which includes code context and log lines sections.

`rca_source` stored on each incident reflects which provider actually ran (e.g. `"gemini (gemini-1.5-flash)"`). Shown as the AI badge in the dashboard detail panel.

### Adding a new LLM provider

1. Implement `BaseLLMProvider` in `sentinel/providers/llm/yourprovider.py` (`complete`, `embed`, `embed_batch`, `health_check`)
2. Add a branch in `sentinel/registry.py::_build_llm()`
3. Add an `if YOUR_API_KEY:` block in `services/dashboard/api.py::_build_llm_chain()`
4. Add `provider: yourprovider` support to `sentinel.yaml`

## Code context builder (`services/dashboard/code_context_builder.py`)

Scans the real repo at runtime to extract relevant buggy code for the RCA prompt:

- Keyword lists per `(service, severity)` in `_KEYWORDS`
- Scores files: +100 bonus for matching `services/{service}/` directory, -50 for dashboard/tests/scripts
- Best line selection: skips docstrings, penalises imports (-0.5), rewards inline bug markers (`←`, `# BUG`, `# FIXME`) with +1.5
- Extracts the enclosing function/class range, not just the single line
- Falls back to GitHub API (`GITHUB_TOKEN` + `GITHUB_REPO`) if local path not found
- Returns `None` if top file score ≤ 50 (no service-path match found)

## CloudWatch alarm receiver

`services/cloudwatch-alarm-receiver/handler.py` — Lambda triggered by SNS:

1. Parses CloudWatch alarm state-change message
2. Derives `service` and `severity` from the alarm name convention: `<severity>-<service>-<metric>`
   - `p1-auth-service-error-rate` → P1, `auth-service`
   - `critical-payments-latency` → P1, `payments`
3. If `SENTINEL_DASHBOARD_URL` is set: POSTs to `/api/incidents/receive` — full AI RCA runs
4. Fallback (dashboard unreachable): writes directly to DynamoDB + emits Kinesis event (no AI RCA)

Terraform in `infra/cloudwatch_alarm_receiver.tf` creates the Lambda, IAM role, SNS topic, and subscription. After `terraform apply`, set the output ARN as the `AlarmActions` target on your CloudWatch alarms.

## Config file (`sentinel.yaml`)

Drop `sentinel.yaml` in the project root (see `sentinel.example.yaml` for full schema). Key sections:

```yaml
llm:
  provider: fallback
  providers:
    - provider: kserve
      endpoint: http://localhost:8081
      model: phi3:mini
    - provider: gemini
      api_key: ${GEMINI_API_KEY}
      model: gemini-1.5-flash
    - provider: anthropic
      api_key: ${ANTHROPIC_API_KEY}
      model: claude-haiku-4-5-20251001

cloud_provider:
  provider: floci         # floci | aws | gcp
  endpoint: http://localhost:4566

git_provider:
  provider: github
  token: ${GITHUB_TOKEN}
  repo: org/repo

alerting:
  provider: slack
  webhook_url: ${SLACK_WEBHOOK_URL}
  channel: "#incidents"
```

Config is loaded via `sentinel.config.loader.load_config(path)` → `SentinelConfig` dataclass.
Providers are wired via `ProviderRegistry.from_config(config_dict)`.

## DynamoDB / Decimal rule

DynamoDB rejects Python `float`. Always use `Decimal(str(value))` for numeric fields going into DynamoDB. Use `_serialise()` in `services/dashboard/api.py` to convert back to `float` for JSON responses.

## Kinesis event structure

```json
{
  "event_type": "SEVERITY_ASSESSED",
  "aggregate_id": "<incident_id>",
  "payload": {
    "incident_id": "...",
    "severity": "P1",
    "rule_severity": "P1",
    "ml_severity": "P1",
    "degradation_trend": "worsening",
    "affected_components": ["auth-service"],
    "log_sample_size": 250,
    "analyzed_at": "2026-05-26T..."
  }
}
```

Streams: `sentinel-alerts`, `sentinel-events`, `sentinel-logs`
Queues: `sentinel-validation-jobs`, `sentinel-confirmed-incidents`

## Step Functions (production RCA pipeline)

`infra/step-functions/incident-response.json` — 5 sequential steps, each calls `root-cause-agent/handler.py`:

1. `fetch_recent_changes` — last 10 GitHub commits before incident time
2. `query_rag` — embed incident summary, similarity-search past incidents from pgvector
3. `analyze_root_cause` — LLM prompt with logs + commits + similar incidents → JSON RCA
4. `generate_runbook` — LLM prompt → step-by-step runbook
5. `publish_report` — save to DynamoDB, emit to Kinesis

Note: the dashboard runs its own lighter RCA pipeline directly (no Step Functions). Step Functions is the production path invoked via the Lambda stack.

## External observability (optional)

Sentinel connects to any external Grafana / Mimir / Loki stack over HTTPS. Configure under `observability:` in `sentinel.yaml`. Push the included dashboard:

```bash
sentinel grafana push observability/grafana/dashboards/sentinel-incidents.json
```

## Tests

```bash
pytest tests/                        # unit tests
python scripts/e2e_test.py           # 29 integration tests against Floci (needs Floci running)
```

Test files: `test_severity.py`, `test_incident.py`, `test_config_loader.py`, `test_provider_registry.py`, `test_loki_bridge.py`, `test_observability_config.py`.

Env override pattern for config tests: `SENTINEL_LLM_MODEL` → `llm.model`. `SENTINEL_LLM_API_KEY` → `llm.api.key` — the loader does a flat `replace("_", ".")` after stripping the prefix, so three underscores = three levels.

## Git workflow

- Branch naming: `feature/slug`
- Commit style: human, lowercase, conversational — not `feat(x): do y`
- No Co-Authored-By lines
- Merge to main + push after each feature branch

## Infra

```bash
cd infra
terraform init
terraform apply \
  -var="github_token=$GITHUB_TOKEN" \
  -var="kserve_endpoint=http://your-cluster:8081" \
  -var="sentinel_dashboard_url=https://sentinel.internal"
```

Outputs include `cloudwatch_alarms_sns_topic_arn` — set this as `AlarmActions` on your CloudWatch alarms.

Helm chart in `helm/sentinel/` — deploys the dashboard + bridge to any K8s cluster.

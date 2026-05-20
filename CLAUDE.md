# Sentinel Framework — CLAUDE.md

AI-powered incident response SaaS framework. Plug into any cloud, any git provider, any LLM. Run `./setup_demo.sh` to go from zero to a running dashboard in one command.

## Quick orientation

```
sentinel/          Core Python package (pip-installable as sentinel-framework)
  config/          YAML config loader → typed dataclasses
  core/            Severity enum, Incident dataclass, PR review logic
  providers/       Abstract base classes + concrete implementations
    base/          BaseLLMProvider, BaseCloudProvider, BaseGitProvider, BaseAlertingProvider
    llm/           anthropic.py · openai.py · kserve.py · ollama.py
    cloud/         aws.py · gcp.py
    git/           github.py · gitlab.py
    alerting/      slack.py · pagerduty.py
  rag/             Codebase indexer → embedder → pgvector store → similarity query
  registry.py      ProviderRegistry.from_config() — single place that wires providers

services/          Lambda handlers + local servers
  dashboard/       FastAPI REST API + single-page UI (Tailwind + vanilla JS)
  kserve-local/    Local KServe V2 bridge → Ollama (runs on port 8080)
  log-analyzer/    Kinesis consumer: rule-based + ML severity classification
  root-cause-agent/ Step Functions handler: 5-step LLM RCA pipeline
  validator/       Validates alerts against CloudWatch / metrics signals
  pr-security-agent/ Scans PRs for secrets, vuln deps, OWASP issues
  loki-bridge/     Lambda: AlertManager webhook → Kinesis (for Loki integration)
  shared/          aws_clients.py (shared boto3 factory used by all Lambdas)

infra/             Terraform — all AWS resources (Kinesis, SQS, DynamoDB, Lambda, Step Functions)
helm/sentinel/     Helm chart for deploying Sentinel to Kubernetes
ml-core/           KServe InferenceService YAML, MLflow training pipeline, phi-3-lite ISVC
observability/     Prometheus values, Grafana dashboard JSON, Loki alert rules, Jaeger values
tests/             pytest suite
scripts/           bootstrap_floci.py, e2e_test.py, rewrite_history.py
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
4. Starts Ollama daemon, picks best available instruction model (prefers `phi3:mini`), starts KServe bridge on port 8080
5. Creates Kinesis streams / SQS queues / DynamoDB tables in Floci
6. Seeds 6 realistic demo incidents + 8 validation records
7. Starts FastAPI dashboard on port 8501

To skip Ollama and use fallback RCA text: just don't have Ollama installed — it degrades gracefully.

## Running components individually

```bash
# Dashboard only (needs Floci running)
./run_dashboard.sh

# KServe bridge only (needs Ollama running)
OLLAMA_MODEL=phi3:mini \
PYTHONPATH=services/kserve-local \
  uvicorn server:app --app-dir services/kserve-local --host 0.0.0.0 --port 8080

# Bootstrap Floci resources only
python scripts/bootstrap_floci.py

# End-to-end test suite (needs Floci running)
python scripts/e2e_test.py

# Unit tests
pytest tests/
```

## Key environment variables

| Variable | Default | Purpose |
|---|---|---|
| `FLOCI_ENDPOINT` | `http://localhost:4566` | Local AWS emulator URL |
| `AWS_DEFAULT_REGION` | `us-east-1` | AWS region |
| `KSERVE_ENDPOINT` | `http://localhost:8080` | KServe bridge URL |
| `KSERVE_MODEL` | `phi3:mini` | Ollama model to use |
| `INCIDENTS_TABLE` | `sentinel-incidents` | DynamoDB table |
| `VALIDATION_RESULTS_TABLE` | `sentinel-validation-results` | DynamoDB table |
| `PR_REVIEWS_TABLE` | `sentinel-pr-reviews` | DynamoDB table |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama daemon (inside kserve-local bridge) |

## Config file (`sentinel.yaml`)

Drop `sentinel.yaml` in the project root (see `sentinel.example.yaml` for the full schema). Key sections:

```yaml
llm:
  provider: kserve        # kserve | openai | anthropic | ollama
  endpoint: http://localhost:8080
  model: phi3:mini

cloud_provider:
  provider: floci         # floci | aws | gcp
  endpoint: http://localhost:4566

git_provider:
  provider: github
  token: ${GITHUB_TOKEN}
  repo: org/repo
```

Config is loaded via `sentinel.config.loader.load_config(path)` → returns a `SentinelConfig` dataclass. Providers are wired via `ProviderRegistry.from_config(config_dict)`.

## Provider abstraction

All providers implement abstract base classes in `sentinel/providers/base/`. To add a new LLM:
1. Implement `BaseLLMProvider` (`complete`, `embed`, `embed_batch`, `health_check`)
2. Add a branch in `registry.py::_build_llm()`
3. Add `provider: yourname` to `sentinel.yaml`

No other code changes needed — the pipeline is provider-agnostic throughout.

## DynamoDB / Decimal rule

DynamoDB rejects Python `float`. Always use `Decimal(str(value))` for numeric fields going into DynamoDB. Use `_serialise()` in `services/dashboard/api.py` to convert back to `float` for JSON responses.

## Demo fire → AI RCA flow

`POST /api/demo/fire` in `services/dashboard/api.py`:
1. Writes incident to DynamoDB immediately with placeholder `"⏳ AI analysis in progress…"`
2. Spawns a background thread that calls `KSERVE_ENDPOINT/v1/models/{model}:predict`
3. KServe bridge (`services/kserve-local/server.py`) forwards to Ollama
4. Background thread parses JSON from model, updates DynamoDB with real RCA + runbook
5. Dashboard auto-refreshes every 15 s and shows the AI content when ready
6. Falls back to hardcoded RCA text if KServe/Ollama is unreachable

## Kinesis event structure

All services communicate via Kinesis. A severity event looks like:
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
    "analyzed_at": "2026-05-24T..."
  }
}
```

Streams: `sentinel-alerts`, `sentinel-events`, `sentinel-logs`
Queues: `sentinel-validation-jobs`, `sentinel-confirmed-incidents`

## Step Functions (root cause pipeline)

`infra/step-functions/incident-response.json` — 5 sequential steps, each calls `root-cause-agent/handler.py` with a different `action`:

1. `fetch_recent_changes` — last 10 GitHub commits before incident
2. `query_rag` — embed incident summary, similarity-search past incidents from pgvector
3. `analyze_root_cause` — LLM prompt with logs + commits + similar incidents → JSON RCA
4. `generate_runbook` — LLM prompt → step-by-step runbook
5. `publish_report` — save to DynamoDB, emit to Kinesis

## External observability (optional)

Sentinel runs on AWS but connects to any external Grafana / Mimir / Loki stack over HTTPS. Configure in `sentinel.yaml` under `observability:`. Push the included dashboard:

```bash
sentinel grafana push observability/grafana/dashboards/sentinel-incidents.json
```

## Tests

```bash
pytest tests/                        # all unit tests
python scripts/e2e_test.py           # 29 integration tests against Floci (needs Floci running)
```

Test files: `test_severity.py`, `test_incident.py`, `test_config_loader.py`, `test_provider_registry.py`, `test_loki_bridge.py`, `test_observability_config.py`.

Env override pattern for config tests: `SENTINEL_LLM_MODEL` → `llm.model` (single underscore = one level). `SENTINEL_LLM_API_KEY` → `llm.api.key` (three levels, not two — the loader does a flat `replace("_", ".")` after stripping the prefix).

## Git workflow

- Branch naming: `feature/slug`
- Commit style: human, lowercase, conversational — not `feat(x): do y`
- No Co-Authored-By lines
- Merge to main + push after each feature branch

## Infra

Terraform in `infra/`. Real AWS deployment:
```bash
cd infra
terraform init
terraform apply -var="github_token=$GITHUB_TOKEN" -var="kserve_endpoint=http://your-cluster:8080"
```

Helm chart in `helm/sentinel/` — deploys the dashboard + bridge to any K8s cluster.

# Sentinel Framework

**AI-powered incident response — plug into any cloud, any git provider, any LLM.**

Sentinel watches your production alerts, confirms they are real failures, runs a multi-step root-cause analysis with an LLM of your choice, generates a runbook, and posts everything to a live dashboard — all in under two minutes.

```
Alert fired
    │
Kinesis stream
    │
┌───▼──────────────────┐
│  Validator Lambda    │  3-signal cross-check: health endpoint + smoke test + metrics
└───┬──────────────────┘
    │ confirmed real incident
┌───▼──────────────────┐
│  Log Analyzer        │  rule-based + ML severity (P1–P4), impact scope, degradation trend
└───┬──────────────────┘
    │
┌───▼──────────────────┐
│  Root Cause Agent    │  Step Functions: recent commits → RAG query → LLM RCA → runbook
│  (5-step pipeline)   │
└───┬──────────────────┘
    │
┌───▼──────────────────┐
│  Dashboard + Slack   │  FastAPI SPA auto-refreshes every 15 s; P1/P2 → Slack alert
└──────────────────────┘
```

Also ships a **PR Security Agent** — every pull request is scanned for OWASP issues, missed edge cases, and structural bugs before merge.

---

## Quick start (60 seconds)

```bash
git clone https://github.com/VishalVinayRam/Project-KEMM
cd Project-KEMM
./setup_demo.sh
# then open http://localhost:8501
```

`setup_demo.sh` is idempotent. It:
1. Checks prereqs (Python 3.10+, pip, Docker)
2. Installs Python deps
3. Starts **Floci** (local AWS emulator) at `http://localhost:4566`
4. Starts **Ollama** daemon, picks the best available model, starts the KServe bridge on port 8080
5. Creates Kinesis streams / SQS queues / DynamoDB tables
6. Seeds 6 realistic demo incidents
7. Starts the FastAPI dashboard on **port 8501**

> **No Ollama?** No problem — Sentinel degrades gracefully to a hardcoded RCA stub so the demo still works.

To fire a test incident from the dashboard, click **"Fire Demo Incident"** or call the API:

```bash
curl -s -X POST http://localhost:8501/api/demo/fire \
  -H "Content-Type: application/json" \
  -d '{"severity": "P1", "service": "auth-service"}' | jq .
```

---

## Provider configuration (`sentinel.yaml`)

Drop a `sentinel.yaml` in the project root (see `sentinel.example.yaml` for the full schema):

```yaml
llm:
  provider: kserve          # kserve | openai | anthropic | ollama | gemini
  endpoint: http://localhost:8080
  model: phi3:mini

cloud_provider:
  provider: floci           # floci | aws | gcp
  endpoint: http://localhost:4566

git_provider:
  provider: github
  token: ${GITHUB_TOKEN}
  repo: org/repo

alerting:
  provider: slack
  webhook_url: ${SLACK_WEBHOOK_URL}
```

### Supported providers

| Category | Providers |
|---|---|
| **LLM** | KServe (Ollama bridge), OpenAI, Anthropic, Gemini, Ollama (direct) |
| **Cloud / storage** | AWS (DynamoDB, Kinesis, S3, SQS, SNS), GCP, Floci (local dev) |
| **Git** | GitHub, GitLab |
| **Alerting** | Slack, PagerDuty |
| **Log ingestion** | CloudWatch Alarms (SNS→Lambda), Loki/Grafana (AlertManager webhook) |

**LLM fallback chain** — if KServe is unavailable, Sentinel automatically falls back through `gemini-1.5-flash → gpt-4o-mini → claude-haiku`. Set any combination of `GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`; only the ones you set are tried.

---

## Key environment variables

| Variable | Default | Purpose |
|---|---|---|
| `SENTINEL_API_KEY` | _(unset = open)_ | Enforces `X-API-Key` auth on all API routes |
| `SENTINEL_ENV` | `development` | Set to `production` to restrict CORS origins |
| `SENTINEL_ALLOWED_ORIGINS` | _(none)_ | Comma-separated allowed CORS origins |
| `FLOCI_ENDPOINT` | `http://localhost:4566` | Local AWS emulator URL |
| `KSERVE_ENDPOINT` | `http://localhost:8081` | KServe / Ollama bridge URL |
| `GEMINI_API_KEY` | _(none)_ | Fallback LLM — Gemini |
| `OPENAI_API_KEY` | _(none)_ | Fallback LLM — OpenAI |
| `ANTHROPIC_API_KEY` | _(none)_ | Fallback LLM — Anthropic |
| `SLACK_WEBHOOK_URL` | _(none)_ | P1/P2 Slack notifications |
| `INCIDENTS_TABLE` | `sentinel-incidents` | DynamoDB table |

---

## Repository layout

```
sentinel/          Core Python package (pip-installable)
  config/          YAML config loader → typed dataclasses
  core/            Severity enum, Incident dataclass, PR review logic
  providers/
    base/          Abstract base classes (LLM, Cloud, Git, Alerting)
    llm/           anthropic · openai · gemini · kserve · ollama · fallback
    cloud/         aws · gcp
    git/           github · gitlab
    alerting/      slack · pagerduty
  rag/             Codebase indexer → pgvector similarity search
  registry.py      ProviderRegistry.from_config() — wires everything

services/          Lambda handlers + local servers
  dashboard/       FastAPI REST API + single-page dashboard UI
  cloudwatch-alarm-receiver/  SNS → Lambda → incident receiver
  log-analyzer/    Kinesis consumer: rule + ML severity classification
  loki-bridge/     AlertManager webhook → Kinesis
  validator/       3-signal alert validation
  root-cause-agent/ Step Functions: 5-step LLM RCA pipeline
  pr-security-agent/ GitHub webhook → OWASP/edge-case PR scan
  kserve-local/    Local KServe V2 bridge → Ollama

infra/             Terraform — all AWS resources
helm/sentinel/     Helm chart for Kubernetes deployment
ml-core/           KServe ISVC YAML, MLflow training pipeline
observability/     Prometheus values, Grafana dashboards, Loki rules
tests/             pytest suite (189 tests, 0 dependencies on real AWS)
```

---

## Running tests

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest tests/                        # 189 unit tests, no infrastructure needed
python scripts/e2e_test.py           # 29 integration tests (needs Floci running)
```

---

## Real AWS deployment

```bash
cd infra
terraform init
terraform apply \
  -var="github_token=$GITHUB_TOKEN" \
  -var="kserve_endpoint=http://your-cluster:8080"
```

Helm chart for Kubernetes:

```bash
helm install sentinel helm/sentinel/ \
  --set sentinelApiKey=$SENTINEL_API_KEY \
  --set kserveEndpoint=http://your-kserve:8080
```

---

## Adding a new LLM provider

1. Implement `BaseLLMProvider` in `sentinel/providers/llm/yourprovider.py` (four methods: `complete`, `embed`, `embed_batch`, `health_check`)
2. Add a branch in `sentinel/registry.py::_build_llm()`
3. Set `llm.provider: yourprovider` in `sentinel.yaml`

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full guide.

---

## License

MIT — see [LICENSE](LICENSE).

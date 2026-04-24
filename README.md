# Sentinel — AI-Powered Incident Response & Code Intelligence Platform

A distributed, event-driven engineering intelligence platform that automates incident detection, root cause analysis, and pre-deployment code review using local AWS infrastructure (via Floci) and Kubernetes-based ML inference.

## Architecture

```
Pre-Deployment                    Production
─────────────                     ──────────
PR opened                         Alert fired
    │                                 │
API Gateway                       Kinesis Stream
    │                                 │
Security Agent ──► PR Comment     Validation Lambda
(Phi-3/KServe)                       │
    │                         ┌──────┴──────┐
RAG Knowledge Base         Real?        False Positive
(pgvector + Redis)            │              │
                         Log Analyzer    DynamoDB (logged)
                         (severity P1-P4)
                              │
                         Step Functions
                         (root cause orchestration)
                              │
                    ┌─────────┴──────────┐
                 KServe               RAG Query
               (Phi-3)           (past incidents)
                    │
               Runbook + Fix Recommendation
                    │
              Slack / Dashboard
```

## Sub-systems

| Sub-system | Description |
|---|---|
| **Pre-deployment Security Agent** | Reviews every PR for vulnerabilities, missed edge cases, and code structure issues before merge |
| **RAG Knowledge Base** | Codebase + docs + past incidents indexed as embeddings — all agents query this for context |
| **Incident Validation Pipeline** | Confirms alerts are real failures (not noise) via automated smoke tests |
| **Log Severity Estimator** | Classifies incident severity (P1–P4), impact scope, and degradation rate from log streams |
| **Root Cause & Resolution Agent** | Orchestrates multi-step analysis: recent PRs → RAG query → Phi-3 analysis → runbook generation |

## Tech Stack

### AWS (via Floci — local emulator)
- API Gateway, Lambda, Kinesis, DynamoDB, S3, SQS, SNS
- RDS (PostgreSQL + pgvector), ElastiCache (Redis)
- Step Functions, EventBridge, Cognito, KMS, Secrets Manager
- CloudWatch, Logs

### Kubernetes (Minikube)
- KServe — Phi-3 model inference
- MLflow — experiment tracking + model registry
- Kubeflow Pipelines + Training Operator
- External Secrets Operator

### Observability
- Prometheus + Grafana — metrics and dashboards
- Jaeger — distributed tracing
- Loki — log aggregation

### Platform
- Terraform — all Floci resources as IaC
- ArgoCD — GitOps model deployments
- GitHub Actions — CI/CD

## Repository Structure

```
sentinel/
├── infra/              # Terraform modules for all AWS resources (Floci)
│   └── modules/
│       ├── s3/         # Codebase snapshots, incident logs, model artifacts
│       ├── dynamodb/   # Incidents, PR reviews, validation results, event store
│       ├── kinesis/    # Alerts, logs, events streams
│       ├── sqs/        # Validation jobs, log ingestion, notifications
│       ├── rds/        # PostgreSQL + pgvector for RAG
│       └── elasticache/# Redis — embedding cache + session state
│
├── services/           # Lambda functions (Python)
│   ├── ingestion/      # Alert/log ingestion into Kinesis
│   ├── validator/      # Smoke tests to confirm real incidents
│   ├── log-analyzer/   # Log severity classification
│   ├── inference-proxy/# Bridge between Lambda and KServe
│   └── root-cause-agent/ # Step Functions orchestrator handler
│
├── ml-core/            # Kubernetes ML stack (original KEMM)
│   ├── train_pipeline.py       # QLoRA fine-tuning with MLflow
│   ├── phi-3-lite-isvc.yaml    # KServe InferenceService
│   ├── mlflow.yaml             # MLflow StatefulSet
│   └── step-a-secrets.yaml    # External Secrets Operator
│
├── gitops/             # ArgoCD application manifests
├── observability/      # Prometheus, Grafana, Jaeger config
└── docs/               # Architecture deep-dives
```

## Getting Started

### Prerequisites
- [Floci](https://floci.io) running locally
- Minikube with KServe + MLflow deployed (see `ml-core/`)
- Terraform >= 1.6
- Python 3.10+

### 1. Start Floci
```bash
# Verify Floci is running
curl http://localhost:4566/_floci/health
```

### 2. Provision AWS Resources
```bash
cd infra
terraform init
terraform plan
terraform apply
```

### 3. Deploy ML Core
```bash
kubectl apply -f ml-core/mlflow.yaml
kubectl apply -f ml-core/step-a-secrets.yaml
kubectl apply -f ml-core/phi-3-lite-isvc.yaml
```

## Build Phases

| Phase | Status | Description |
|---|---|---|
| 1 | ✅ In Progress | Repo structure + Terraform base (S3, DynamoDB, Kinesis, SQS, RDS, ElastiCache) |
| 2 | ⬜ Pending | Pre-deployment Security Agent (webhook → Lambda → KServe → PR comment) |
| 3 | ⬜ Pending | RAG Knowledge Base (codebase → embeddings → pgvector) |
| 4 | ⬜ Pending | Incident Validation Pipeline (Kinesis → smoke tests → SQS) |
| 5 | ⬜ Pending | Log Severity Estimation (Phi-3 classifies log streams) |
| 6 | ⬜ Pending | Root Cause Step Functions Orchestration |
| 7 | ⬜ Pending | Full Observability (Prometheus, Grafana, Jaeger) |
| 8 | ⬜ Pending | ArgoCD GitOps + MLflow model retraining loop |

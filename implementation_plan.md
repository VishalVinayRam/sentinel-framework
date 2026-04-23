# Implementation Plan - Lite MLOps Pipeline

This plan outlines the steps to deploy a standalone MLOps pipeline on Minikube, including training an LLM with QLoRA and serving it via KServe.

## Phase 1: Infrastructure Deployment
- [x] Install External Secrets Operator using Helm.
- [x] Deploy Kubeflow Pipelines (Standalone).
- [x] Deploy Kubeflow Training Operator (Standalone).
- [x] Deploy MLflow (StatefulSet + SQLite).
- [x] Install cert-manager and KServe.
- [ ] Patch KServe for RawDeployment mode (verified/applied config).

## Phase 2: Security & Secrets Management (Step A)
- [x] Configure `SecretStore` to mock a cloud provider. (Used Fake provider for mock)
- [x] Create `ExternalSecret` to inject `HF_TOKEN`.
- [x] Verify secret injection in a test pod. (Successfully synced `hf-token` in `ml-ops-lite`)

## Phase 3: Training Pipeline (Step B)
- [ ] Write Python training script with `@dsl.component`.
- [ ] Implement QLoRA training for Phi-3-mini or TinyLlama.
- [ ] Configure MLflow tracking (autologging + adapter saving).
- [ ] Compile and run the KFP pipeline.

## Phase 4: Serving & Model Merge (Step C)
- [ ] Create KServe `InferenceService` manifest.
- [ ] Point `storageUri` to MLflow artifact path.
- [ ] Implement/Configure the model merge logic for loading base + adapters.

## Phase 5: Validation
- [ ] Test the inference endpoint.
- [ ] Verify disk and VRAM usage constraints.

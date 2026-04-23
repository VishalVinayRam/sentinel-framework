# Lite MLOps Pipeline Walkthrough

This document explains the "Lite" MLOps pipeline implemented on Minikube.

## 1. Core Stack Deployment
The infrastructure is deployed using standalone manifests to minimize footprint:
- **Kubeflow Pipelines (KFP)**: Standalone install (backend + UI).
- **Training Operator**: Standalone (PyTorchJob support).
- **MLflow**: StatefulSet with SQLite backend on a shared PVC.
- **KServe**: Installed in `RawDeployment` mode (no Istio/Knative).

## 2. Security Layer (External Secrets)
We use the **External Secrets Operator (ESO)** to inject the `HF_TOKEN`:
- A `SecretStore` mocks a cloud provider.
- An `ExternalSecret` retrieves the token from the mock provider and injects it as a Kubernetes Secret named `hf-token`.
- No hardcoded keys are present in any manifests.

## 3. Training Workflow (QLoRA)
The training script [train_pipeline.py](file:///home/linuxlite/Desktop/test/ml-ops/train_pipeline.py) implements:
- **4-bit Quantization**: Uses `bitsandbytes` to load Phi-3-mini/TinyLlama in ~4GB VRAM.
- **MLflow Tracking**: Integrated with `mlflow.autolog()`.
- **Adapter Saving**: Only LoRA adapters (~50MB) are saved to MLflow, avoiding multi-GB weight transfers.

## 4. Serving (The Model Merge Challenge)
The [InferenceService](file:///home/linuxlite/Desktop/test/ml-ops/phi-3-lite-isvc.yaml) setup:
- Uses KServe's `HuggingFace` runtime.
- **Model Merge**: Configured via `MODEL_ID` (base model) and `storageUri` (pointer to adapters on the MLflow PVC).
- KServe runtime automatically handles the PEFT merge during startup.

## Next Steps
1. Wait for `cert-manager` and `kserve` pods to be ready.
2. Compile and run the KFP pipeline using `kfp.compiler`.
3. Deploy the ISVC and test the endpoint.

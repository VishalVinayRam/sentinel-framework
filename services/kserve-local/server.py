#!/usr/bin/env python3
"""
Local KServe V2 bridge — translates KServe inference protocol to Ollama.

Lets the full Sentinel pipeline run locally without a real K8s/KServe cluster.
Any model name in the URL path is accepted; inference always uses OLLAMA_MODEL.

Run:
    OLLAMA_MODEL=phi3:mini uvicorn server:app --host 0.0.0.0 --port 8080
"""
import logging
import os

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

OLLAMA_URL   = os.environ.get("OLLAMA_URL",   "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "phi3:mini")

log = logging.getLogger(__name__)

app = FastAPI(title="Sentinel KServe Bridge", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/v2/health/ready")
def health_ready():
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        if r.status_code == 200:
            return {"ready": True}
    except Exception:
        pass
    raise HTTPException(status_code=503, detail="Ollama not reachable")


@app.get("/v2/health/live")
def health_live():
    return {"live": True}


@app.get("/v2/models")
def list_models():
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        models = [m["name"] for m in r.json().get("models", [])]
    except Exception:
        models = [OLLAMA_MODEL]
    return {"models": [{"name": m} for m in models]}


@app.post("/v1/models/{model_name}:predict")
def predict(model_name: str, body: dict):
    """Forward a KServe V2 predict request to Ollama's generate API."""
    inputs = body.get("inputs", [])
    if not inputs:
        raise HTTPException(status_code=400, detail="No inputs provided")

    prompt = inputs[0]["data"][0]
    params = body.get("parameters", {})

    try:
        ollama_body = {
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {
                "num_predict": int(params.get("max_new_tokens", 512)),
                "temperature": float(params.get("temperature", 0.2)),
                "top_p": 0.9,
            },
        }
        # Forward system prompt if provided — helps small models follow JSON instructions
        if "system" in params:
            ollama_body["system"] = params["system"]

        resp = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json=ollama_body,
            timeout=180,
        )
        resp.raise_for_status()
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail=f"Ollama not reachable at {OLLAMA_URL}")
    except Exception as e:
        log.error("Ollama request failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))

    text = resp.json().get("response", "")
    return {
        "model_name": OLLAMA_MODEL,
        "outputs": [
            {
                "name":     "text_output",
                "shape":    [1],
                "datatype": "BYTES",
                "data":     [text],
            }
        ],
    }


@app.post("/v1/models/{model_name}-embed:predict")
def embed(model_name: str, body: dict):
    """Forward embedding requests to Ollama's embeddings API."""
    try:
        text = body["inputs"][0]["data"][0]
        resp = requests.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": OLLAMA_MODEL, "prompt": text},
            timeout=30,
        )
        resp.raise_for_status()
        vector = resp.json()["embedding"]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {
        "outputs": [
            {"name": "embedding", "shape": [len(vector)], "datatype": "FP32", "data": vector}
        ]
    }

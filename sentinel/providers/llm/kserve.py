import requests

from sentinel.providers.base.llm import BaseLLMProvider, LLMResponse


class KServeProvider(BaseLLMProvider):
    """LLM provider backed by KServe (supports any HuggingFace-compatible model)."""

    def __init__(self, endpoint: str, model_name: str = "phi-3-lite", timeout: int = 60):
        self._endpoint = endpoint.rstrip("/")
        self._model = model_name
        self._timeout = timeout

    def complete(self, prompt: str, max_tokens: int = 1024, temperature: float = 0.2) -> LLMResponse:
        resp = requests.post(
            f"{self._endpoint}/v1/models/{self._model}:predict",
            json={
                "inputs": [{
                    "name": "text_input",
                    "shape": [1],
                    "datatype": "BYTES",
                    "data": [prompt],
                }],
                "parameters": {
                    "max_new_tokens": max_tokens,
                    "temperature": temperature,
                },
            },
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["outputs"][0]["data"][0]
        return LLMResponse(text=text, model=self._model, raw=data)

    def embed(self, text: str) -> list[float]:
        resp = requests.post(
            f"{self._endpoint}/v1/models/{self._model}-embed:predict",
            json={"inputs": [{"name": "text_input", "shape": [1], "datatype": "BYTES", "data": [text]}]},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return resp.json()["outputs"][0]["data"]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]

    def health_check(self) -> bool:
        try:
            resp = requests.get(f"{self._endpoint}/v2/health/ready", timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

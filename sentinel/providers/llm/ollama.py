import requests

from sentinel.providers.base.llm import BaseLLMProvider, LLMResponse

OLLAMA_API = "http://localhost:11434"


class OllamaProvider(BaseLLMProvider):
    """LLM provider backed by Ollama (phi3, llama3, mistral, etc.)."""

    def __init__(self, base_url: str = OLLAMA_API, model: str = "phi3:mini", timeout: int = 120):
        self._url   = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout

    def complete(self, prompt: str, max_tokens: int = 1024, temperature: float = 0.2) -> LLMResponse:
        resp = requests.post(
            f"{self._url}/api/generate",
            json={
                "model":  self._model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "num_predict": max_tokens,
                    "temperature": temperature,
                    "top_p": 0.9,
                },
            },
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return LLMResponse(text=data["response"], model=self._model, raw=data)

    def embed(self, text: str) -> list[float]:
        resp = requests.post(
            f"{self._url}/api/embeddings",
            json={"model": self._model, "prompt": text},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return resp.json()["embedding"]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]

    def health_check(self) -> bool:
        try:
            resp = requests.get(f"{self._url}/api/tags", timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

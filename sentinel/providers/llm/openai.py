import requests

from sentinel.providers.base.llm import BaseLLMProvider, LLMResponse

OPENAI_API = "https://api.openai.com/v1"


class OpenAIProvider(BaseLLMProvider):
    """LLM provider backed by OpenAI (gpt-4o, gpt-4-turbo, etc.)."""

    def __init__(self, api_key: str, model: str = "gpt-4o-mini", timeout: int = 60):
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    def complete(self, prompt: str, max_tokens: int = 1024, temperature: float = 0.2) -> LLMResponse:
        resp = requests.post(
            f"{OPENAI_API}/chat/completions",
            headers=self._headers,
            json={
                "model": self._model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        tokens = data["usage"]["total_tokens"]
        return LLMResponse(text=text, model=self._model, tokens_used=tokens, raw=data)

    def embed(self, text: str) -> list[float]:
        resp = requests.post(
            f"{OPENAI_API}/embeddings",
            headers=self._headers,
            json={"model": "text-embedding-3-small", "input": text},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        resp = requests.post(
            f"{OPENAI_API}/embeddings",
            headers=self._headers,
            json={"model": "text-embedding-3-small", "input": texts},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return [item["embedding"] for item in resp.json()["data"]]

    def health_check(self) -> bool:
        try:
            resp = requests.get(f"{OPENAI_API}/models", headers=self._headers, timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

import requests

from sentinel.providers.base.llm import BaseLLMProvider, LLMResponse

ANTHROPIC_API = "https://api.anthropic.com/v1"


class AnthropicProvider(BaseLLMProvider):
    """LLM provider backed by Anthropic Claude (claude-3-5-sonnet, etc.)."""

    def __init__(self, api_key: str, model: str = "claude-haiku-4-5-20251001", timeout: int = 60):
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    def complete(self, prompt: str, max_tokens: int = 1024, temperature: float = 0.2) -> LLMResponse:
        resp = requests.post(
            f"{ANTHROPIC_API}/messages",
            headers=self._headers,
            json={
                "model": self._model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["content"][0]["text"]
        tokens = data["usage"]["input_tokens"] + data["usage"]["output_tokens"]
        return LLMResponse(text=text, model=self._model, tokens_used=tokens, raw=data)

    def embed(self, text: str) -> list[float]:
        raise NotImplementedError("Anthropic does not offer an embeddings API. Use OpenAI or a local model.")

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError("Anthropic does not offer an embeddings API. Use OpenAI or a local model.")

    def health_check(self) -> bool:
        try:
            resp = requests.get(f"{ANTHROPIC_API}/models", headers=self._headers, timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

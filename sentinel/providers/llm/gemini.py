import requests

from sentinel.providers.base.llm import BaseLLMProvider, LLMResponse

GEMINI_API = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiProvider(BaseLLMProvider):
    """LLM provider backed by Google Gemini (gemini-1.5-flash, gemini-1.5-pro, etc.)."""

    def __init__(self, api_key: str, model: str = "gemini-1.5-flash", timeout: int = 60):
        self._api_key = api_key
        self._model = model
        self._timeout = timeout

    def _url(self, action: str) -> str:
        return f"{GEMINI_API}/{self._model}:{action}?key={self._api_key}"

    def complete(self, prompt: str, max_tokens: int = 1024, temperature: float = 0.2) -> LLMResponse:
        resp = requests.post(
            self._url("generateContent"),
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": temperature,
                    "maxOutputTokens": max_tokens,
                    "responseMimeType": "text/plain",
                },
            },
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        usage = data.get("usageMetadata", {})
        tokens = usage.get("totalTokenCount", 0)
        return LLMResponse(text=text, model=self._model, tokens_used=tokens, raw=data)

    def embed(self, text: str) -> list[float]:
        resp = requests.post(
            f"{GEMINI_API}/text-embedding-004:embedContent?key={self._api_key}",
            json={"model": "models/text-embedding-004", "content": {"parts": [{"text": text}]}},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return resp.json()["embedding"]["values"]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]

    def health_check(self) -> bool:
        try:
            resp = requests.get(
                f"{GEMINI_API}?key={self._api_key}",
                timeout=5,
            )
            return resp.status_code == 200
        except Exception:
            return False

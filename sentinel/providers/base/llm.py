from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class LLMResponse:
    text: str
    model: str
    tokens_used: int = 0
    raw: dict = None

    def as_json(self) -> dict:
        import json
        try:
            return json.loads(self.text)
        except json.JSONDecodeError:
            return {"raw_text": self.text}


class BaseLLMProvider(ABC):
    """Abstract interface all LLM providers must implement.

    Implement this to support KServe, OpenAI, Anthropic, Ollama, etc.
    """

    @abstractmethod
    def complete(self, prompt: str, max_tokens: int = 1024, temperature: float = 0.2) -> LLMResponse:
        """Send a prompt and return a completion."""

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        """Return an embedding vector for the given text."""

    @abstractmethod
    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Return embedding vectors for a batch of texts."""

    @abstractmethod
    def health_check(self) -> bool:
        """Return True if the LLM backend is reachable."""

    def complete_as_json(self, prompt: str, **kwargs) -> dict:
        response = self.complete(prompt, **kwargs)
        return response.as_json()

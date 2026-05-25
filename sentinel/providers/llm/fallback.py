import logging

from sentinel.providers.base.llm import BaseLLMProvider, LLMResponse

logger = logging.getLogger(__name__)


class FallbackLLMProvider(BaseLLMProvider):
    """Tries providers in order, returning the first successful response.

    Usage:
        chain = FallbackLLMProvider([kserve_provider, gemini_provider, openai_provider])
        response = chain.complete(prompt)  # tries each until one succeeds
    """

    def __init__(self, providers: list[BaseLLMProvider]):
        if not providers:
            raise ValueError("FallbackLLMProvider requires at least one provider")
        self._providers = providers

    def complete(self, prompt: str, max_tokens: int = 1024, temperature: float = 0.2) -> LLMResponse:
        last_error = None
        for provider in self._providers:
            name = type(provider).__name__
            try:
                if not provider.health_check():
                    logger.info("[fallback] %s health_check failed — skipping", name)
                    continue
                result = provider.complete(prompt, max_tokens=max_tokens, temperature=temperature)
                logger.info("[fallback] %s succeeded", name)
                return result
            except Exception as e:
                logger.warning("[fallback] %s failed: %s — trying next", name, e)
                last_error = e
        raise RuntimeError(f"All providers in fallback chain failed. Last error: {last_error}")

    def embed(self, text: str) -> list[float]:
        for provider in self._providers:
            try:
                return provider.embed(text)
            except (NotImplementedError, Exception):
                continue
        raise RuntimeError("No provider in chain supports embed()")

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        for provider in self._providers:
            try:
                return provider.embed_batch(texts)
            except (NotImplementedError, Exception):
                continue
        raise RuntimeError("No provider in chain supports embed_batch()")

    def health_check(self) -> bool:
        return any(p.health_check() for p in self._providers)

    @property
    def active_provider_name(self) -> str:
        """Return the name of the first healthy provider."""
        for p in self._providers:
            try:
                if p.health_check():
                    return type(p).__name__
            except Exception:
                continue
        return "none"

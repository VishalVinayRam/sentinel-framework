"""Unit tests for all LLM providers.

Uses the `responses` library to intercept requests.get/post — no real HTTP.
"""
import pytest
import responses as rsps_lib
from requests.exceptions import ConnectionError

from sentinel.providers.base.llm import LLMResponse
from sentinel.providers.llm.anthropic import AnthropicProvider, ANTHROPIC_API
from sentinel.providers.llm.openai import OpenAIProvider, OPENAI_API
from sentinel.providers.llm.gemini import GeminiProvider, GEMINI_API
from sentinel.providers.llm.kserve import KServeProvider
from sentinel.providers.llm.ollama import OllamaProvider
from sentinel.providers.llm.fallback import FallbackLLMProvider


# ── Anthropic ───────────────────────────────────────────────────────────────

class TestAnthropicProvider:
    def setup_method(self):
        self.provider = AnthropicProvider(api_key="test-key", model="claude-test")

    @rsps_lib.activate
    def test_complete_happy_path(self):
        rsps_lib.add(rsps_lib.POST, f"{ANTHROPIC_API}/messages", json={
            "content": [{"text": "root cause found"}],
            "usage": {"input_tokens": 10, "output_tokens": 20},
        })
        result = self.provider.complete("diagnose this")
        assert isinstance(result, LLMResponse)
        assert result.text == "root cause found"
        assert result.tokens_used == 30
        assert result.model == "claude-test"

    @rsps_lib.activate
    def test_complete_api_error_raises(self):
        rsps_lib.add(rsps_lib.POST, f"{ANTHROPIC_API}/messages", status=429, json={"error": "rate limited"})
        with pytest.raises(Exception):
            self.provider.complete("diagnose this")

    @rsps_lib.activate
    def test_health_check_ok(self):
        rsps_lib.add(rsps_lib.GET, f"{ANTHROPIC_API}/models", status=200, json={"models": []})
        assert self.provider.health_check() is True

    @rsps_lib.activate
    def test_health_check_unreachable(self):
        rsps_lib.add(rsps_lib.GET, f"{ANTHROPIC_API}/models", body=ConnectionError())
        assert self.provider.health_check() is False

    def test_embed_raises_not_implemented(self):
        with pytest.raises(NotImplementedError):
            self.provider.embed("text")

    def test_embed_batch_raises_not_implemented(self):
        with pytest.raises(NotImplementedError):
            self.provider.embed_batch(["text"])


# ── OpenAI ──────────────────────────────────────────────────────────────────

class TestOpenAIProvider:
    def setup_method(self):
        self.provider = OpenAIProvider(api_key="sk-test", model="gpt-test")

    @rsps_lib.activate
    def test_complete_happy_path(self):
        rsps_lib.add(rsps_lib.POST, f"{OPENAI_API}/chat/completions", json={
            "choices": [{"message": {"content": "memory leak in cache"}}],
            "usage": {"total_tokens": 50},
        })
        result = self.provider.complete("what went wrong")
        assert result.text == "memory leak in cache"
        assert result.tokens_used == 50

    @rsps_lib.activate
    def test_complete_http_error_raises(self):
        rsps_lib.add(rsps_lib.POST, f"{OPENAI_API}/chat/completions", status=500)
        with pytest.raises(Exception):
            self.provider.complete("what went wrong")

    @rsps_lib.activate
    def test_embed_happy_path(self):
        rsps_lib.add(rsps_lib.POST, f"{OPENAI_API}/embeddings", json={
            "data": [{"embedding": [0.1, 0.2, 0.3]}]
        })
        vec = self.provider.embed("some text")
        assert vec == [0.1, 0.2, 0.3]

    @rsps_lib.activate
    def test_embed_batch(self):
        rsps_lib.add(rsps_lib.POST, f"{OPENAI_API}/embeddings", json={
            "data": [{"embedding": [0.1]}, {"embedding": [0.2]}]
        })
        vecs = self.provider.embed_batch(["a", "b"])
        assert len(vecs) == 2

    @rsps_lib.activate
    def test_health_check_ok(self):
        rsps_lib.add(rsps_lib.GET, f"{OPENAI_API}/models", status=200, json={"data": []})
        assert self.provider.health_check() is True

    @rsps_lib.activate
    def test_health_check_down(self):
        rsps_lib.add(rsps_lib.GET, f"{OPENAI_API}/models", body=ConnectionError())
        assert self.provider.health_check() is False


# ── Gemini ──────────────────────────────────────────────────────────────────

class TestGeminiProvider:
    def setup_method(self):
        self.provider = GeminiProvider(api_key="goog-test", model="gemini-test")

    @rsps_lib.activate
    def test_complete_happy_path(self):
        url = f"{GEMINI_API}/gemini-test:generateContent"
        rsps_lib.add(rsps_lib.POST, url, json={
            "candidates": [{"content": {"parts": [{"text": "cpu spike detected"}]}}],
            "usageMetadata": {"totalTokenCount": 40},
        }, match_querystring=False)
        result = self.provider.complete("analyze")
        assert result.text == "cpu spike detected"
        assert result.tokens_used == 40

    @rsps_lib.activate
    def test_complete_api_error_raises(self):
        url = f"{GEMINI_API}/gemini-test:generateContent"
        rsps_lib.add(rsps_lib.POST, url, status=403, match_querystring=False)
        with pytest.raises(Exception):
            self.provider.complete("analyze")

    @rsps_lib.activate
    def test_health_check_ok(self):
        rsps_lib.add(rsps_lib.GET, GEMINI_API, status=200, json={"models": []}, match_querystring=False)
        assert self.provider.health_check() is True

    @rsps_lib.activate
    def test_health_check_unreachable(self):
        rsps_lib.add(rsps_lib.GET, GEMINI_API, body=ConnectionError(), match_querystring=False)
        assert self.provider.health_check() is False


# ── KServe ──────────────────────────────────────────────────────────────────

KSERVE_BASE = "http://kserve-test:8080"


class TestKServeProvider:
    def setup_method(self):
        self.provider = KServeProvider(endpoint=KSERVE_BASE, model_name="phi3", timeout=10)

    @rsps_lib.activate
    def test_complete_happy_path(self):
        rsps_lib.add(rsps_lib.POST, f"{KSERVE_BASE}/v1/models/phi3:predict", json={
            "outputs": [{"data": ["db pool exhausted"]}]
        })
        result = self.provider.complete("diagnose")
        assert result.text == "db pool exhausted"
        assert result.model == "phi3"

    @rsps_lib.activate
    def test_complete_server_error_raises(self):
        rsps_lib.add(rsps_lib.POST, f"{KSERVE_BASE}/v1/models/phi3:predict", status=503)
        with pytest.raises(Exception):
            self.provider.complete("diagnose")

    @rsps_lib.activate
    def test_health_check_ready(self):
        rsps_lib.add(rsps_lib.GET, f"{KSERVE_BASE}/v2/health/ready", status=200)
        assert self.provider.health_check() is True

    @rsps_lib.activate
    def test_health_check_not_ready(self):
        rsps_lib.add(rsps_lib.GET, f"{KSERVE_BASE}/v2/health/ready", status=503)
        assert self.provider.health_check() is False

    @rsps_lib.activate
    def test_health_check_connection_error(self):
        rsps_lib.add(rsps_lib.GET, f"{KSERVE_BASE}/v2/health/ready", body=ConnectionError())
        assert self.provider.health_check() is False


# ── Ollama ──────────────────────────────────────────────────────────────────

class TestOllamaProvider:
    def setup_method(self):
        self.provider = OllamaProvider(base_url="http://ollama-test:11434", model="llama3:1b")

    @rsps_lib.activate
    def test_complete_happy_path(self):
        rsps_lib.add(rsps_lib.POST, "http://ollama-test:11434/api/generate", json={
            "response": "timeout in payment gateway"
        })
        result = self.provider.complete("explain the failure")
        assert result.text == "timeout in payment gateway"
        assert result.model == "llama3:1b"

    @rsps_lib.activate
    def test_complete_connection_error_raises(self):
        rsps_lib.add(rsps_lib.POST, "http://ollama-test:11434/api/generate", body=ConnectionError())
        with pytest.raises(Exception):
            self.provider.complete("explain")

    @rsps_lib.activate
    def test_health_check_ok(self):
        rsps_lib.add(rsps_lib.GET, "http://ollama-test:11434/api/tags", status=200, json={"models": []})
        assert self.provider.health_check() is True

    @rsps_lib.activate
    def test_health_check_unreachable(self):
        rsps_lib.add(rsps_lib.GET, "http://ollama-test:11434/api/tags", body=ConnectionError())
        assert self.provider.health_check() is False


# ── FallbackLLMProvider ─────────────────────────────────────────────────────

class _AlwaysHealthy:
    """Stub provider that always passes health check and returns a canned response."""
    def health_check(self): return True
    def complete(self, prompt, **kw): return LLMResponse(text="ok from healthy", model="stub")
    def embed(self, text): return [0.0]
    def embed_batch(self, texts): return [[0.0]] * len(texts)


class _AlwaysDead:
    """Stub provider that always fails health check and raises on complete."""
    def health_check(self): return False
    def complete(self, prompt, **kw): raise RuntimeError("provider down")
    def embed(self, text): raise NotImplementedError
    def embed_batch(self, texts): raise NotImplementedError


class _HealthyButCompleteFails:
    """Stub provider: health_check passes but complete raises."""
    def health_check(self): return True
    def complete(self, prompt, **kw): raise RuntimeError("inference error")
    def embed(self, text): raise NotImplementedError
    def embed_batch(self, texts): raise NotImplementedError


class TestFallbackLLMProvider:
    def test_requires_at_least_one_provider(self):
        with pytest.raises(ValueError):
            FallbackLLMProvider([])

    def test_first_healthy_provider_is_used(self):
        chain = FallbackLLMProvider([_AlwaysHealthy(), _AlwaysHealthy()])
        result = chain.complete("prompt")
        assert result.text == "ok from healthy"

    def test_skips_dead_provider_uses_second(self):
        dead    = _AlwaysDead()
        healthy = _AlwaysHealthy()
        chain   = FallbackLLMProvider([dead, healthy])
        result  = chain.complete("prompt")
        assert result.text == "ok from healthy"

    def test_skips_provider_whose_complete_raises(self):
        chain = FallbackLLMProvider([_HealthyButCompleteFails(), _AlwaysHealthy()])
        result = chain.complete("prompt")
        assert result.text == "ok from healthy"

    def test_all_providers_fail_raises_runtime_error(self):
        chain = FallbackLLMProvider([_AlwaysDead(), _HealthyButCompleteFails()])
        with pytest.raises(RuntimeError, match="All providers"):
            chain.complete("prompt")

    def test_health_check_true_if_any_healthy(self):
        chain = FallbackLLMProvider([_AlwaysDead(), _AlwaysHealthy()])
        assert chain.health_check() is True

    def test_health_check_false_if_all_dead(self):
        chain = FallbackLLMProvider([_AlwaysDead(), _AlwaysDead()])
        assert chain.health_check() is False

    def test_embed_uses_first_working_provider(self):
        chain = FallbackLLMProvider([_AlwaysDead(), _AlwaysHealthy()])
        vec = chain.embed("text")
        assert vec == [0.0]

    def test_embed_all_fail_raises(self):
        chain = FallbackLLMProvider([_AlwaysDead(), _AlwaysDead()])
        with pytest.raises(RuntimeError):
            chain.embed("text")

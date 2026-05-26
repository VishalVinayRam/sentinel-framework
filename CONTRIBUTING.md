# Contributing to Sentinel Framework

Thanks for your interest in contributing. This guide covers everything you need to get started.

---

## Table of contents

1. [Running tests](#running-tests)
2. [Adding a new LLM provider](#adding-a-new-llm-provider)
3. [Adding a new cloud provider](#adding-a-new-cloud-provider)
4. [Adding a new alerting provider](#adding-a-new-alerting-provider)
5. [PR checklist](#pr-checklist)
6. [Code style](#code-style)

---

## Running tests

```bash
# Install dependencies
pip install -r requirements.txt -r requirements-dev.txt

# Run the unit test suite (no infrastructure needed)
pytest tests/

# Run with coverage
pytest tests/ --cov=sentinel --cov=services/dashboard --cov-report=term-missing

# Run a single test file
pytest tests/test_lambda_handlers.py -v

# Integration tests (requires Floci running)
./setup_demo.sh          # start Floci + dashboard
python scripts/e2e_test.py
```

All tests are pure unit tests using `unittest.mock` — no real AWS, no real HTTP. The CI matrix runs Python 3.10, 3.11, and 3.12 on every PR.

---

## Adding a new LLM provider

All LLM providers implement `sentinel/providers/base/llm.py::BaseLLMProvider`.

**Step 1 — Create the provider file**

```python
# sentinel/providers/llm/myprovider.py
from sentinel.providers.base.llm import BaseLLMProvider, LLMResponse

MY_API = "https://api.myprovider.com/v1"

class MyProvider(BaseLLMProvider):
    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model   = model

    def complete(self, prompt: str, **kwargs) -> LLMResponse:
        # call your API here
        resp = requests.post(f"{MY_API}/completions", ...)
        resp.raise_for_status()
        return LLMResponse(text=resp.json()["text"], model=self.model)

    def embed(self, text: str) -> list[float]:
        raise NotImplementedError  # only needed if your provider supports embeddings

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    def health_check(self) -> bool:
        try:
            return requests.get(f"{MY_API}/models", timeout=3).status_code == 200
        except Exception:
            return False
```

**Step 2 — Register in the registry**

```python
# sentinel/registry.py  — add a branch in _build_llm()
elif cfg.provider == "myprovider":
    from sentinel.providers.llm.myprovider import MyProvider
    return MyProvider(api_key=cfg.api_key, model=cfg.model or "my-default-model")
```

**Step 3 — Write tests**

Add a `TestMyProvider` class in `tests/test_llm_providers.py`. Use the `responses` library to intercept HTTP calls — see the existing `TestAnthropicProvider` as a template.

**Step 4 — Use it**

```yaml
# sentinel.yaml
llm:
  provider: myprovider
  api_key: ${MY_API_KEY}
  model: my-model-name
```

---

## Adding a new cloud provider

All cloud providers implement `sentinel/providers/base/cloud.py::BaseCloudProvider`.

1. Create `sentinel/providers/cloud/myprovider.py`
2. Implement: `get_recent_logs`, `get_metric`, `put_event`, `health_check`
3. Register in `sentinel/registry.py::_build_cloud()`

---

## Adding a new alerting provider

All alerting providers implement `sentinel/providers/base/alerting.py::BaseAlertingProvider`.

1. Create `sentinel/providers/alerting/myprovider.py`
2. Implement: `send_alert`, `resolve_alert`, `health_check`
3. Register in `sentinel/registry.py::_build_alerting()`

---

## PR checklist

Before opening a PR, confirm:

- [ ] `pytest tests/` passes locally with no failures
- [ ] `flake8 sentinel/ services/ tests/ --max-line-length=120` passes
- [ ] `isort --check-only sentinel/ services/ tests/` passes
- [ ] New code has tests (unit tests preferred; integration tests for infrastructure paths)
- [ ] No secrets, tokens, or personal email addresses in committed files
- [ ] PR description explains **why** the change is needed, not just what changed

**Commit style** — lowercase, conversational, no `feat(x):` prefixes:
```
fix rate limiting for demo fire endpoint
add gemini provider with fallback support
improve health check to verify dynamodb reachability
```

---

## Code style

- Line length: 120 characters
- Formatter: `isort` for imports, `flake8` for lint
- No comments that restate what the code does — only comments explaining *why* (hidden constraints, workarounds, non-obvious invariants)
- DynamoDB: always use `Decimal(str(value))` for numeric fields — never `float` (DynamoDB rejects it)
- Error handling: only validate at system boundaries (user input, external APIs); trust internal invariants

---

## Questions?

Open an issue tagged `question` — we respond within 48 hours.

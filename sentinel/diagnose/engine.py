"""Standalone RCA engine — no FastAPI, no DynamoDB, usable from CLI or library."""

import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Optional

OLLAMA_URL        = os.environ.get("OLLAMA_URL",        "http://localhost:11434")
OLLAMA_MODEL      = os.environ.get("OLLAMA_MODEL",      "llama3.2:1b")
KSERVE_ENDPOINT   = os.environ.get("KSERVE_ENDPOINT",   "http://localhost:8081")
KSERVE_MODEL      = os.environ.get("KSERVE_MODEL",      "llama3.2:1b")
GEMINI_API_KEY    = os.environ.get("GEMINI_API_KEY",    "")
GEMINI_MODEL      = os.environ.get("GEMINI_MODEL",      "gemini-1.5-flash")
OPENAI_API_KEY    = os.environ.get("OPENAI_API_KEY",    "")
OPENAI_MODEL      = os.environ.get("OPENAI_MODEL",      "gpt-4o-mini")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL   = os.environ.get("ANTHROPIC_MODEL",   "claude-haiku-4-5-20251001")


# Minimal prompt for small local models (1b–3b).
# Uses plain English descriptions — template JSON confuses small models.
_PROMPT_SMALL = """\
{service} ({severity} incident){log_section}
Write a JSON object with these string fields:
  root_cause: one sentence — the precise technical reason this broke
  summary: one sentence — business impact on users
  runbook: object with step1 step2 step3 step4 — immediate fix actions
  degradation_trend: "worsening" or "stable" or "improving"
  affected_components: list of service names affected
  confidence: "high" or "medium" or "low"
"""

# Rich prompt for capable models (Gemini Flash, GPT-4o-mini, Claude Haiku+)
_PROMPT_FULL = """\
You are a senior SRE performing root cause analysis on a production incident.

## Incident
- Service: {service}
- Severity: {severity}
- Time: {timestamp}
{log_section}{code_section}
## Task
Analyse this incident and return a JSON object with EXACTLY these fields:
- root_cause: string — precise technical root cause in one sentence
- summary: string — business impact in one sentence
- contributing_factors: list of strings — up to 3 contributing factors
- runbook: object with step1, step2, step3, step4 (strings) — immediate remediation steps
- degradation_trend: "worsening" | "stable" | "improving"
- affected_components: list of strings — affected services/systems
- confidence: "high" | "medium" | "low"

Return ONLY the JSON object. No markdown fences, no explanation."""


def build_providers(
    only: Optional[str] = None,
    verbose: bool = False,
) -> list:
    """Build ordered LLM provider list from env vars.

    Priority: KServe/Ollama (local, free) → Gemini → OpenAI → Anthropic.
    Pass only="anthropic" etc. to force a single provider.
    """
    # Re-read at call time so CLI model overrides applied before this call are honoured
    kserve_endpoint   = os.environ.get("KSERVE_ENDPOINT",   KSERVE_ENDPOINT)
    kserve_model      = os.environ.get("KSERVE_MODEL",      KSERVE_MODEL)
    gemini_key        = os.environ.get("GEMINI_API_KEY",    GEMINI_API_KEY)
    gemini_model      = os.environ.get("GEMINI_MODEL",      GEMINI_MODEL)
    openai_key        = os.environ.get("OPENAI_API_KEY",    OPENAI_API_KEY)
    openai_model      = os.environ.get("OPENAI_MODEL",      OPENAI_MODEL)
    anthropic_key     = os.environ.get("ANTHROPIC_API_KEY", ANTHROPIC_API_KEY)
    anthropic_model   = os.environ.get("ANTHROPIC_MODEL",   ANTHROPIC_MODEL)

    providers = []

    def _add(builder):
        try:
            p = builder()
            providers.append(p)
            if verbose:
                click_safe_echo(f"  [llm] + {type(p).__name__}")
        except Exception as e:
            if verbose:
                click_safe_echo(f"  [llm] skip: {e}")

    want = only.lower() if only else None
    ollama_url   = os.environ.get("OLLAMA_URL",   OLLAMA_URL)
    ollama_model = os.environ.get("OLLAMA_MODEL", OLLAMA_MODEL)

    # Direct Ollama (uses format=json — most reliable for small models)
    if want in (None, "ollama"):
        _add(lambda: _import("sentinel.providers.llm.ollama", "OllamaProvider")(
            base_url=ollama_url, model=ollama_model))

    # KServe bridge (fallback — used when Ollama isn't directly accessible)
    if want in (None, "kserve"):
        _add(lambda: _import("sentinel.providers.llm.kserve", "KServeProvider")(
            endpoint=kserve_endpoint, model_name=kserve_model))

    if want in (None, "gemini") and gemini_key:
        _add(lambda: _import("sentinel.providers.llm.gemini", "GeminiProvider")(
            api_key=gemini_key, model=gemini_model))

    if want in (None, "openai") and openai_key:
        _add(lambda: _import("sentinel.providers.llm.openai", "OpenAIProvider")(
            api_key=openai_key, model=openai_model))

    if want in (None, "anthropic") and anthropic_key:
        _add(lambda: _import("sentinel.providers.llm.anthropic", "AnthropicProvider")(
            api_key=anthropic_key, model=anthropic_model))

    return providers


def run_rca(
    service: str,
    severity: str,
    log_context: str = "",
    code_context: Optional[dict] = None,
    providers: Optional[list] = None,
) -> tuple[Optional[dict], str]:
    """Run the RCA chain. Returns (parsed_rca_dict, provider_label) or (None, 'none')."""
    if providers is None:
        providers = build_providers()
    if not providers:
        return None, "none"

    log_section = ""
    if log_context:
        excerpt = log_context[-1500:].strip()
        log_section = f"\n## Recent error logs\n```\n{excerpt}\n```\n"

    code_section = ""
    if code_context and code_context.get("files"):
        f = code_context["files"][0]
        snippet = "\n".join(f.get("lines", [])[:30])
        code_section = (
            f"\n## Relevant source code — {f['path']} (line {f.get('start_line', 1)})\n"
            f"```{f.get('language', '')}\n{snippet}\n```\n"
        )

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    last_err: Optional[Exception] = None

    for provider in providers:
        cls_name = type(provider).__name__
        try:
            if not provider.health_check():
                continue

            if cls_name in ("KServeProvider", "OllamaProvider"):
                log_short = log_context[-800:].strip() if log_context else ""
                log_sec   = f"Recent logs:\n{log_short}\n" if log_short else ""
                prompt    = _PROMPT_SMALL.format(service=service, severity=severity, log_section=log_sec)
                max_tok   = 400
            else:
                prompt  = _PROMPT_FULL.format(
                    service=service, severity=severity, timestamp=timestamp,
                    log_section=log_section, code_section=code_section,
                )
                max_tok = 700

            result = provider.complete(prompt, max_tokens=max_tok, temperature=0.1)
            raw    = result.text

            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if not match:
                continue

            parsed = json.loads(match.group())
            # Small models sometimes return strings as single-element lists
            for field in ("root_cause", "summary", "degradation_trend", "confidence"):
                val = parsed.get(field)
                if isinstance(val, list):
                    parsed[field] = val[0] if val else ""
                elif isinstance(val, dict):
                    # Extract first string value found
                    parsed[field] = next((v for v in val.values() if isinstance(v, str)), "")

            rc = str(parsed.get("root_cause", ""))
            if len(rc) < 15:
                continue

            return parsed, _label(provider)

        except Exception as exc:
            last_err = exc
            continue

    return None, "none"


# ── internal helpers ────────────────────────────────────────────────────────

def _import(module: str, cls: str):
    import importlib
    return getattr(importlib.import_module(module), cls)


def _label(provider) -> str:
    ollama_model      = os.environ.get("OLLAMA_MODEL",    OLLAMA_MODEL)
    kserve_model      = os.environ.get("KSERVE_MODEL",    KSERVE_MODEL)
    gemini_model      = os.environ.get("GEMINI_MODEL",    GEMINI_MODEL)
    openai_model      = os.environ.get("OPENAI_MODEL",    OPENAI_MODEL)
    anthropic_model   = os.environ.get("ANTHROPIC_MODEL", ANTHROPIC_MODEL)
    return {
        "OllamaProvider":    f"ollama ({ollama_model})",
        "KServeProvider":    f"kserve-ollama ({kserve_model})",
        "GeminiProvider":    f"gemini ({gemini_model})",
        "OpenAIProvider":    f"openai ({openai_model})",
        "AnthropicProvider": f"anthropic ({anthropic_model})",
    }.get(type(provider).__name__, type(provider).__name__.lower())


def click_safe_echo(msg: str) -> None:
    print(msg, file=sys.stderr)

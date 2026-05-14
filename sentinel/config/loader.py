import os
from pathlib import Path

import yaml

from .schema import (
    AlertingConfig, CloudConfig, GitConfig, GrafanaConfig, IncidentConfig,
    LLMConfig, LokiConfig, MimirConfig, ObservabilityConfig, PRAgentConfig,
    RAGConfig, SentinelConfig,
)

_ENV_PREFIX = "SENTINEL_"


def load(path: str = "sentinel.yaml") -> SentinelConfig:
    """Load and validate config from a sentinel.yaml file.

    Environment variables prefixed with SENTINEL_ override file values.
    Example: SENTINEL_LLM_API_KEY overrides llm.api_key
    """
    raw = _read_file(path)
    raw = _apply_env_overrides(raw)
    return _parse(raw)


def _read_file(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"sentinel.yaml not found at {p.resolve()}. "
            "Run `sentinel init` to generate a starter config."
        )
    with p.open() as f:
        return yaml.safe_load(f) or {}


def _apply_env_overrides(raw: dict) -> dict:
    overrides = {
        k[len(_ENV_PREFIX):].lower().replace("_", "."): v
        for k, v in os.environ.items()
        if k.startswith(_ENV_PREFIX)
    }
    for dotted_key, value in overrides.items():
        parts = dotted_key.split(".")
        target = raw
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = value
    return raw


def _parse(raw: dict) -> SentinelConfig:
    git_raw = raw.get("git_provider", {})
    cloud_raw = raw.get("cloud_provider", {})
    llm_raw = raw.get("llm", {})
    rag_raw = raw.get("rag", {})
    incident_raw = raw.get("incident", {})
    pr_raw = raw.get("pr_agent", {})
    alerting_raw = raw.get("alerting")
    obs_raw = raw.get("observability")

    return SentinelConfig(
        namespace=raw.get("namespace", "default"),
        git_provider=GitConfig(
            provider=git_raw["provider"],
            token=git_raw.get("token", ""),
            repo=git_raw.get("repo", ""),
            webhook_secret=git_raw.get("webhook_secret", ""),
            base_url=git_raw.get("base_url", ""),
        ),
        cloud_provider=CloudConfig(
            provider=cloud_raw["provider"],
            region=cloud_raw.get("region", "us-east-1"),
            project_id=cloud_raw.get("project_id", ""),
            endpoint=cloud_raw.get("endpoint", ""),
        ),
        llm=LLMConfig(
            provider=llm_raw["provider"],
            endpoint=llm_raw.get("endpoint", ""),
            model=llm_raw.get("model", ""),
            api_key=llm_raw.get("api_key", ""),
        ),
        rag=RAGConfig(
            enabled=rag_raw.get("enabled", True),
            index_paths=rag_raw.get("index_paths", ["src/", "docs/"]),
            exclude=rag_raw.get("exclude", ["node_modules/", ".git/"]),
            chunk_size=rag_raw.get("chunk_size", 512),
            chunk_overlap=rag_raw.get("chunk_overlap", 64),
            top_k=rag_raw.get("top_k", 5),
        ),
        incident=IncidentConfig(
            alert_sources=incident_raw.get("alert_sources", ["cloudwatch"]),
            signal_threshold=incident_raw.get("signal_threshold", 2),
            notification=incident_raw.get("notification", []),
            auto_remediate=incident_raw.get("auto_remediate", False),
        ),
        pr_agent=PRAgentConfig(
            enabled=pr_raw.get("enabled", True),
            block_on=pr_raw.get("block_on", ["critical", "high"]),
            max_diff_lines=pr_raw.get("max_diff_lines", 500),
        ),
        alerting=AlertingConfig(**alerting_raw) if alerting_raw else None,
        observability=_parse_observability(obs_raw) if obs_raw else None,
    )


def _parse_observability(raw: dict) -> ObservabilityConfig:
    loki_raw = raw.get("loki")
    mimir_raw = raw.get("mimir")
    grafana_raw = raw.get("grafana")
    return ObservabilityConfig(
        loki=LokiConfig(**loki_raw) if loki_raw else None,
        mimir=MimirConfig(**mimir_raw) if mimir_raw else None,
        grafana=GrafanaConfig(**grafana_raw) if grafana_raw else None,
    )

import os
import tempfile
import pytest
import yaml

from sentinel.config.loader import load
from sentinel.config.schema import SentinelConfig


MINIMAL_CONFIG = {
    "namespace": "test",
    "git_provider": {
        "provider": "github",
        "token": "ghp_test",
        "repo": "owner/repo",
    },
    "cloud_provider": {
        "provider": "aws",
        "region": "us-east-1",
    },
    "llm": {
        "provider": "openai",
        "api_key": "sk-test",
        "model": "gpt-4o-mini",
    },
}


def _write_config(data: dict, path: str):
    with open(path, "w") as f:
        yaml.dump(data, f)


def test_load_minimal_config(tmp_path):
    cfg_file = tmp_path / "sentinel.yaml"
    _write_config(MINIMAL_CONFIG, str(cfg_file))

    cfg = load(str(cfg_file))

    assert isinstance(cfg, SentinelConfig)
    assert cfg.namespace == "test"
    assert cfg.git_provider.provider == "github"
    assert cfg.git_provider.token == "ghp_test"
    assert cfg.cloud_provider.provider == "aws"
    assert cfg.cloud_provider.region == "us-east-1"
    assert cfg.llm.provider == "openai"
    assert cfg.llm.api_key == "sk-test"


def test_load_defaults_for_optional_fields(tmp_path):
    cfg_file = tmp_path / "sentinel.yaml"
    _write_config(MINIMAL_CONFIG, str(cfg_file))

    cfg = load(str(cfg_file))

    assert cfg.rag.enabled is True
    assert cfg.rag.chunk_size == 512
    assert cfg.pr_agent.enabled is True
    assert cfg.alerting is None


def test_missing_file_raises():
    with pytest.raises(FileNotFoundError, match="sentinel.yaml not found"):
        load("/nonexistent/path/sentinel.yaml")


def test_env_override_applies(tmp_path, monkeypatch):
    cfg_file = tmp_path / "sentinel.yaml"
    _write_config(MINIMAL_CONFIG, str(cfg_file))

    # loader does replace("_", ".") so SENTINEL_LLM_MODEL -> llm.model (single depth)
    monkeypatch.setenv("SENTINEL_LLM_MODEL", "gpt-4o")
    cfg = load(str(cfg_file))

    assert cfg.llm.model == "gpt-4o"


def test_env_override_namespace(tmp_path, monkeypatch):
    cfg_file = tmp_path / "sentinel.yaml"
    _write_config(MINIMAL_CONFIG, str(cfg_file))

    monkeypatch.setenv("SENTINEL_NAMESPACE", "production")
    cfg = load(str(cfg_file))

    assert cfg.namespace == "production"


def test_alerting_config_parsed(tmp_path):
    data = dict(MINIMAL_CONFIG)
    data["alerting"] = {
        "provider": "slack",
        "webhook_url": "https://hooks.slack.com/test",
    }
    cfg_file = tmp_path / "sentinel.yaml"
    _write_config(data, str(cfg_file))

    cfg = load(str(cfg_file))

    assert cfg.alerting is not None
    assert cfg.alerting.provider == "slack"
    assert cfg.alerting.webhook_url == "https://hooks.slack.com/test"
    assert cfg.alerting.api_key == ""


def test_rag_config_custom_values(tmp_path):
    data = dict(MINIMAL_CONFIG)
    data["rag"] = {
        "enabled": False,
        "chunk_size": 256,
        "top_k": 10,
    }
    cfg_file = tmp_path / "sentinel.yaml"
    _write_config(data, str(cfg_file))

    cfg = load(str(cfg_file))

    assert cfg.rag.enabled is False
    assert cfg.rag.chunk_size == 256
    assert cfg.rag.top_k == 10

"""Tests for the observability config section (loki/mimir/grafana)."""

import yaml
from sentinel.config.loader import load
from sentinel.config.schema import GrafanaConfig, LokiConfig, MimirConfig, ObservabilityConfig


BASE = {
    "namespace": "test",
    "git_provider": {"provider": "github", "token": "t", "repo": "o/r"},
    "cloud_provider": {"provider": "aws", "region": "us-east-1"},
    "llm": {"provider": "openai", "api_key": "k", "model": "gpt-4o-mini"},
}


def _write(tmp_path, data):
    p = tmp_path / "sentinel.yaml"
    p.write_text(yaml.dump(data))
    return str(p)


def test_no_observability_block_gives_none(tmp_path):
    cfg = load(_write(tmp_path, BASE))
    assert cfg.observability is None


def test_full_observability_block_parsed(tmp_path):
    data = dict(BASE)
    data["observability"] = {
        "loki":    {"url": "https://loki.example.com:3100", "tenant_id": "team-a"},
        "mimir":   {"url": "https://mimir.example.com/api/v1/push", "username": "u", "password": "p"},
        "grafana": {"url": "https://grafana.example.com", "api_key": "glsa_xxx", "folder": "Ops"},
    }
    cfg = load(_write(tmp_path, data))

    obs = cfg.observability
    assert isinstance(obs, ObservabilityConfig)

    assert isinstance(obs.loki, LokiConfig)
    assert obs.loki.url == "https://loki.example.com:3100"
    assert obs.loki.tenant_id == "team-a"

    assert isinstance(obs.mimir, MimirConfig)
    assert obs.mimir.username == "u"
    assert obs.mimir.metric_prefix == "sentinel"  # default

    assert isinstance(obs.grafana, GrafanaConfig)
    assert obs.grafana.folder == "Ops"
    assert obs.grafana.api_key == "glsa_xxx"


def test_partial_observability_only_grafana(tmp_path):
    data = dict(BASE)
    data["observability"] = {
        "grafana": {"url": "https://g.example.com", "api_key": "k"},
    }
    cfg = load(_write(tmp_path, data))
    assert cfg.observability.loki is None
    assert cfg.observability.mimir is None
    assert cfg.observability.grafana is not None


def test_loki_defaults(tmp_path):
    data = dict(BASE)
    data["observability"] = {"loki": {"url": "http://loki:3100"}}
    cfg = load(_write(tmp_path, data))
    loki = cfg.observability.loki
    assert loki.username == ""
    assert loki.password == ""
    assert loki.tenant_id == ""


def test_mimir_default_metric_prefix(tmp_path):
    data = dict(BASE)
    data["observability"] = {"mimir": {"url": "http://mimir:9009/api/v1/push"}}
    cfg = load(_write(tmp_path, data))
    assert cfg.observability.mimir.metric_prefix == "sentinel"


def test_grafana_default_folder(tmp_path):
    data = dict(BASE)
    data["observability"] = {"grafana": {"url": "http://grafana:3000", "api_key": "k"}}
    cfg = load(_write(tmp_path, data))
    assert cfg.observability.grafana.folder == "Sentinel"

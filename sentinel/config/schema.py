from dataclasses import dataclass, field
from typing import Optional


@dataclass
class GitConfig:
    provider: str                    # github | gitlab | bitbucket
    token: str
    repo: str
    webhook_secret: str = ""
    base_url: str = ""               # for self-hosted GitLab


@dataclass
class CloudConfig:
    provider: str                    # aws | gcp | azure | floci
    region: str = "us-east-1"
    project_id: str = ""             # GCP
    endpoint: str = ""               # Floci override


@dataclass
class LLMConfig:
    provider: str                    # kserve | openai | anthropic | ollama
    endpoint: str = ""               # for kserve / ollama
    model: str = ""
    api_key: str = ""                # for openai / anthropic


@dataclass
class RAGConfig:
    enabled: bool = True
    index_paths: list = field(default_factory=lambda: ["src/", "docs/"])
    exclude: list = field(default_factory=lambda: ["node_modules/", ".git/", "__pycache__/"])
    chunk_size: int = 512
    chunk_overlap: int = 64
    top_k: int = 5


@dataclass
class IncidentConfig:
    alert_sources: list = field(default_factory=lambda: ["cloudwatch"])
    signal_threshold: int = 2        # number of failing signals to confirm real incident
    notification: list = field(default_factory=list)
    auto_remediate: bool = False


@dataclass
class PRAgentConfig:
    enabled: bool = True
    block_on: list = field(default_factory=lambda: ["critical", "high"])
    max_diff_lines: int = 500


@dataclass
class AlertingConfig:
    provider: str = ""               # slack | pagerduty | opsgenie | email
    webhook_url: str = ""
    api_key: str = ""


@dataclass
class SentinelConfig:
    git_provider: GitConfig
    cloud_provider: CloudConfig
    llm: LLMConfig
    rag: RAGConfig = field(default_factory=RAGConfig)
    incident: IncidentConfig = field(default_factory=IncidentConfig)
    pr_agent: PRAgentConfig = field(default_factory=PRAgentConfig)
    alerting: Optional[AlertingConfig] = None
    namespace: str = "default"       # used for multi-tenant resource isolation

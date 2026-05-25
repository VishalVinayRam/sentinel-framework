from typing import Optional

from sentinel.providers.base.cloud import BaseCloudProvider
from sentinel.providers.base.git import BaseGitProvider
from sentinel.providers.base.llm import BaseLLMProvider
from sentinel.providers.base.alerting import BaseAlertingProvider


class ProviderRegistry:
    """Single registry that holds all configured providers.

    Injected into every Sentinel service so they stay provider-agnostic.
    Any provider can be swapped without touching service logic.
    """

    def __init__(
        self,
        cloud: BaseCloudProvider,
        git: BaseGitProvider,
        llm: BaseLLMProvider,
        alerting: Optional[BaseAlertingProvider] = None,
    ):
        self.cloud = cloud
        self.git = git
        self.llm = llm
        self.alerting = alerting

    def validate(self) -> list[str]:
        """Return list of unhealthy providers. Empty list means all healthy."""
        issues = []
        if not self.llm.health_check():
            issues.append(f"LLM provider ({type(self.llm).__name__}) is unreachable")
        if self.alerting and not self.alerting.health_check():
            issues.append(f"Alerting provider ({type(self.alerting).__name__}) is unreachable")
        return issues

    @classmethod
    def from_config(cls, config: dict) -> "ProviderRegistry":
        """Build a registry from a parsed sentinel.yaml config dict."""
        cloud = _build_cloud(config["cloud_provider"])
        git = _build_git(config["git_provider"])
        llm = _build_llm(config["llm"])
        alerting = _build_alerting(config.get("alerting")) if config.get("alerting") else None
        return cls(cloud=cloud, git=git, llm=llm, alerting=alerting)


def _build_cloud(cfg: dict) -> BaseCloudProvider:
    provider = cfg["provider"]
    if provider == "aws":
        from sentinel.providers.cloud.aws import AWSProvider
        return AWSProvider(region=cfg.get("region", "us-east-1"))
    if provider == "floci":
        from sentinel.providers.cloud.aws import AWSProvider
        return AWSProvider(region=cfg.get("region", "us-east-1"), endpoint_url=cfg.get("endpoint", "http://localhost:4566"))
    if provider == "gcp":
        from sentinel.providers.cloud.gcp import GCPProvider
        return GCPProvider(project_id=cfg["project_id"])
    raise ValueError(f"Unknown cloud provider: {provider!r}")


def _build_git(cfg: dict) -> BaseGitProvider:
    provider = cfg["provider"]
    if provider == "github":
        from sentinel.providers.git.github import GitHubProvider
        return GitHubProvider(token=cfg["token"], webhook_secret=cfg.get("webhook_secret", ""))
    if provider == "gitlab":
        from sentinel.providers.git.gitlab import GitLabProvider
        return GitLabProvider(token=cfg["token"], webhook_secret=cfg.get("webhook_secret", ""), base_url=cfg.get("base_url", "https://gitlab.com/api/v4"))
    raise ValueError(f"Unknown git provider: {provider!r}")


def _build_llm(cfg: dict) -> BaseLLMProvider:
    provider = cfg["provider"]
    if provider == "kserve":
        from sentinel.providers.llm.kserve import KServeProvider
        return KServeProvider(endpoint=cfg["endpoint"], model_name=cfg.get("model", "phi-3-lite"))
    if provider == "openai":
        from sentinel.providers.llm.openai import OpenAIProvider
        return OpenAIProvider(api_key=cfg["api_key"], model=cfg.get("model", "gpt-4o-mini"))
    if provider == "anthropic":
        from sentinel.providers.llm.anthropic import AnthropicProvider
        return AnthropicProvider(api_key=cfg["api_key"], model=cfg.get("model", "claude-haiku-4-5-20251001"))
    if provider == "gemini":
        from sentinel.providers.llm.gemini import GeminiProvider
        return GeminiProvider(api_key=cfg["api_key"], model=cfg.get("model", "gemini-1.5-flash"))
    if provider == "fallback":
        from sentinel.providers.llm.fallback import FallbackLLMProvider
        return FallbackLLMProvider([_build_llm(p) for p in cfg["providers"]])
    raise ValueError(f"Unknown LLM provider: {provider!r}")


def _build_alerting(cfg: dict) -> Optional[BaseAlertingProvider]:
    if not cfg:
        return None
    provider = cfg.get("provider")
    if provider == "slack":
        from sentinel.providers.alerting.slack import SlackProvider
        return SlackProvider(webhook_url=cfg["webhook_url"], channel=cfg.get("channel", ""))
    if provider == "pagerduty":
        from sentinel.providers.alerting.pagerduty import PagerDutyProvider
        return PagerDutyProvider(routing_key=cfg["routing_key"])
    return None

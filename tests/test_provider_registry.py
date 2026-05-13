import pytest
from unittest.mock import MagicMock, patch

from sentinel.registry import ProviderRegistry
from sentinel.providers.base.cloud import BaseCloudProvider
from sentinel.providers.base.git import BaseGitProvider
from sentinel.providers.base.llm import BaseLLMProvider
from sentinel.providers.base.alerting import BaseAlertingProvider


def _mock_cloud():
    m = MagicMock(spec=BaseCloudProvider)
    return m


def _mock_git():
    return MagicMock(spec=BaseGitProvider)


def _mock_llm(healthy=True):
    m = MagicMock(spec=BaseLLMProvider)
    m.health_check.return_value = healthy
    return m


def _mock_alerting(healthy=True):
    m = MagicMock(spec=BaseAlertingProvider)
    m.health_check.return_value = healthy
    return m


def test_registry_stores_providers():
    cloud = _mock_cloud()
    git = _mock_git()
    llm = _mock_llm()

    reg = ProviderRegistry(cloud=cloud, git=git, llm=llm)

    assert reg.cloud is cloud
    assert reg.git is git
    assert reg.llm is llm
    assert reg.alerting is None


def test_registry_with_alerting():
    alerting = _mock_alerting()
    reg = ProviderRegistry(
        cloud=_mock_cloud(), git=_mock_git(), llm=_mock_llm(), alerting=alerting
    )
    assert reg.alerting is alerting


def test_validate_all_healthy_returns_empty():
    reg = ProviderRegistry(
        cloud=_mock_cloud(), git=_mock_git(), llm=_mock_llm(healthy=True)
    )
    issues = reg.validate()
    assert issues == []


def test_validate_unhealthy_llm():
    reg = ProviderRegistry(
        cloud=_mock_cloud(), git=_mock_git(), llm=_mock_llm(healthy=False)
    )
    issues = reg.validate()
    assert len(issues) == 1
    assert "LLM" in issues[0]


def test_validate_unhealthy_alerting():
    reg = ProviderRegistry(
        cloud=_mock_cloud(),
        git=_mock_git(),
        llm=_mock_llm(healthy=True),
        alerting=_mock_alerting(healthy=False),
    )
    issues = reg.validate()
    assert any("Alerting" in i for i in issues)


def test_validate_no_alerting_skips_alerting_check():
    llm = _mock_llm(healthy=True)
    reg = ProviderRegistry(cloud=_mock_cloud(), git=_mock_git(), llm=llm)
    # should not raise even though alerting is None
    issues = reg.validate()
    assert issues == []


@patch("sentinel.providers.cloud.aws.AWSProvider")
@patch("sentinel.providers.git.github.GitHubProvider")
@patch("sentinel.providers.llm.openai.OpenAIProvider")
def test_from_config_github_aws_openai(mock_openai, mock_github, mock_aws):
    config = {
        "cloud_provider": {"provider": "aws", "region": "us-east-1"},
        "git_provider": {"provider": "github", "token": "ghp_test", "webhook_secret": ""},
        "llm": {"provider": "openai", "api_key": "sk-test", "model": "gpt-4o-mini"},
    }
    reg = ProviderRegistry.from_config(config)
    assert reg is not None
    mock_aws.assert_called_once()
    mock_github.assert_called_once()
    mock_openai.assert_called_once()


@patch("sentinel.providers.cloud.aws.AWSProvider")
@patch("sentinel.providers.git.github.GitHubProvider")
@patch("sentinel.providers.llm.openai.OpenAIProvider")
@patch("sentinel.providers.alerting.slack.SlackProvider")
def test_from_config_with_slack_alerting(mock_slack, mock_openai, mock_github, mock_aws):
    config = {
        "cloud_provider": {"provider": "aws", "region": "us-east-1"},
        "git_provider": {"provider": "github", "token": "t", "webhook_secret": ""},
        "llm": {"provider": "openai", "api_key": "k", "model": "gpt-4o-mini"},
        "alerting": {"provider": "slack", "webhook_url": "https://hooks.slack.com/x"},
    }
    reg = ProviderRegistry.from_config(config)
    assert reg is not None
    mock_slack.assert_called_once()


def test_from_config_unknown_cloud_raises():
    config = {
        "cloud_provider": {"provider": "azure"},
        "git_provider": {"provider": "github", "token": "t"},
        "llm": {"provider": "openai", "api_key": "k"},
    }
    with pytest.raises(ValueError, match="Unknown cloud provider"):
        ProviderRegistry.from_config(config)


def test_from_config_unknown_llm_raises():
    with patch("sentinel.providers.cloud.aws.AWSProvider"), \
         patch("sentinel.providers.git.github.GitHubProvider"):
        config = {
            "cloud_provider": {"provider": "aws"},
            "git_provider": {"provider": "github", "token": "t"},
            "llm": {"provider": "bedrock", "api_key": "k"},
        }
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            ProviderRegistry.from_config(config)

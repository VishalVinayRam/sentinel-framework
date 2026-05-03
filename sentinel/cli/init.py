import os
from pathlib import Path

import click
import yaml


@click.command()
@click.option("--output", "-o", default="sentinel.yaml", help="Where to write the config file")
@click.option("--non-interactive", is_flag=True, help="Use defaults without prompting")
def init_command(output: str, non_interactive: bool):
    """Generate a sentinel.yaml config for your project."""

    click.echo("\n🛡️  Sentinel Setup\n")

    if Path(output).exists() and not non_interactive:
        if not click.confirm(f"  {output} already exists. Overwrite?", default=False):
            click.echo("  Aborted.")
            return

    if non_interactive:
        config = _default_config()
    else:
        config = _interactive_config()

    with open(output, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    click.echo(f"\n  ✅  Config written to {output}")
    click.echo("  Next steps:")
    click.echo("    1. Fill in your tokens in the config (or set env vars like SENTINEL_GIT_PROVIDER_TOKEN)")
    click.echo("    2. Run `sentinel validate` to check everything is wired up")
    click.echo("    3. Run `terraform -chdir=infra apply` to provision cloud resources\n")


def _interactive_config() -> dict:
    click.echo("  Answer a few questions to get started.\n")

    git_provider = click.prompt(
        "  Git provider",
        type=click.Choice(["github", "gitlab"]),
        default="github",
    )
    git_repo = click.prompt("  Repository (owner/repo)", default="my-org/my-repo")
    git_token = click.prompt("  Git token (leave blank to set via env var)", default="", hide_input=True)

    cloud_provider = click.prompt(
        "\n  Cloud provider",
        type=click.Choice(["floci", "aws", "gcp"]),
        default="floci",
    )
    cloud_region = "us-east-1"
    cloud_endpoint = ""
    gcp_project = ""
    if cloud_provider == "gcp":
        gcp_project = click.prompt("  GCP project ID", default="my-gcp-project")
    elif cloud_provider == "floci":
        cloud_endpoint = click.prompt("  Floci endpoint", default="http://localhost:4566")

    llm_provider = click.prompt(
        "\n  LLM provider",
        type=click.Choice(["kserve", "openai", "anthropic"]),
        default="kserve",
    )
    llm_endpoint = ""
    llm_model = ""
    llm_api_key = ""
    if llm_provider == "kserve":
        llm_endpoint = click.prompt("  KServe endpoint", default="http://localhost:8080")
        llm_model = click.prompt("  Model name", default="phi-3-lite")
    elif llm_provider == "openai":
        llm_model = click.prompt("  Model", default="gpt-4o-mini")
        llm_api_key = click.prompt("  API key (leave blank to use OPENAI_API_KEY)", default="", hide_input=True)
    elif llm_provider == "anthropic":
        llm_model = click.prompt("  Model", default="claude-haiku-4-5-20251001")
        llm_api_key = click.prompt("  API key (leave blank to use ANTHROPIC_API_KEY)", default="", hide_input=True)

    alerting_provider = click.prompt(
        "\n  Alerting channel",
        type=click.Choice(["slack", "pagerduty", "none"]),
        default="slack",
    )
    alerting_cfg = {}
    if alerting_provider == "slack":
        alerting_cfg = {
            "provider": "slack",
            "webhook_url": click.prompt("  Slack webhook URL", default="${SLACK_WEBHOOK_URL}"),
        }
    elif alerting_provider == "pagerduty":
        alerting_cfg = {
            "provider": "pagerduty",
            "routing_key": click.prompt("  PagerDuty routing key", default="${PAGERDUTY_ROUTING_KEY}"),
        }

    namespace = click.prompt("\n  Namespace (for multi-tenant isolation)", default="default")

    return _build_config(
        git_provider=git_provider, git_repo=git_repo, git_token=git_token or "${GITHUB_TOKEN}",
        cloud_provider=cloud_provider, cloud_region=cloud_region, cloud_endpoint=cloud_endpoint, gcp_project=gcp_project,
        llm_provider=llm_provider, llm_endpoint=llm_endpoint, llm_model=llm_model, llm_api_key=llm_api_key or f"${{{llm_provider.upper()}_API_KEY}}",
        alerting=alerting_cfg, namespace=namespace,
    )


def _default_config() -> dict:
    return _build_config(
        git_provider="github", git_repo="my-org/my-repo", git_token="${GITHUB_TOKEN}",
        cloud_provider="floci", cloud_region="us-east-1", cloud_endpoint="http://localhost:4566", gcp_project="",
        llm_provider="kserve", llm_endpoint="http://localhost:8080", llm_model="phi-3-lite", llm_api_key="",
        alerting={"provider": "slack", "webhook_url": "${SLACK_WEBHOOK_URL}"}, namespace="default",
    )


def _build_config(**kw) -> dict:
    cfg = {
        "namespace": kw["namespace"],
        "git_provider": {
            "provider": kw["git_provider"],
            "token": kw["git_token"],
            "repo": kw["git_repo"],
            "webhook_secret": "${GITHUB_WEBHOOK_SECRET}",
        },
        "cloud_provider": {"provider": kw["cloud_provider"], "region": kw["cloud_region"]},
        "llm": {"provider": kw["llm_provider"]},
        "rag": {"enabled": True, "index_paths": ["src/", "docs/"], "exclude": ["node_modules/", ".git/"], "top_k": 5},
        "pr_agent": {"enabled": True, "block_on": ["critical", "high"]},
        "incident": {"alert_sources": ["cloudwatch"], "signal_threshold": 2},
    }
    if kw["cloud_provider"] == "floci":
        cfg["cloud_provider"]["endpoint"] = kw["cloud_endpoint"]
    if kw["cloud_provider"] == "gcp":
        cfg["cloud_provider"]["project_id"] = kw["gcp_project"]
    if kw["llm_provider"] == "kserve":
        cfg["llm"]["endpoint"] = kw["llm_endpoint"]
        cfg["llm"]["model"] = kw["llm_model"]
    else:
        cfg["llm"]["model"] = kw["llm_model"]
        cfg["llm"]["api_key"] = kw["llm_api_key"]
    if kw["alerting"]:
        cfg["alerting"] = kw["alerting"]
    return cfg

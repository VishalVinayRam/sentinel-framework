import click


@click.command()
@click.option("--config", "-c", default="sentinel.yaml", help="Path to sentinel.yaml")
def validate_command(config: str):
    """Validate sentinel.yaml and check all providers are reachable."""
    from sentinel.config.loader import load
    from sentinel.registry import ProviderRegistry

    click.echo(f"\n  Loading config from {config}...")
    try:
        cfg = load(config)
    except FileNotFoundError as e:
        click.echo(f"  ❌  {e}")
        raise SystemExit(1)
    except Exception as e:
        click.echo(f"  ❌  Config error: {e}")
        raise SystemExit(1)

    click.echo("  ✅  Config parsed successfully")
    click.echo(f"      namespace: {cfg.namespace}")
    click.echo(f"      git:       {cfg.git_provider.provider} / {cfg.git_provider.repo}")
    click.echo(f"      cloud:     {cfg.cloud_provider.provider}")
    click.echo(f"      llm:       {cfg.llm.provider} ({cfg.llm.model or cfg.llm.endpoint})")

    click.echo("\n  Checking provider health...")
    try:
        registry = ProviderRegistry.from_config(cfg.__dict__)
        issues = registry.validate()
    except Exception as e:
        click.echo(f"  ❌  Failed to build providers: {e}")
        raise SystemExit(1)

    if issues:
        for issue in issues:
            click.echo(f"  ⚠️   {issue}")
        click.echo("\n  Some providers are unreachable. Check your config and try again.\n")
    else:
        click.echo("  ✅  All providers reachable\n")

import click


@click.command()
@click.option("--config", "-c", default="sentinel.yaml")
@click.option("--last", "-n", default=5, help="How many recent incidents to show")
def status_command(config: str, last: int):
    """Show recent incidents and system health."""
    from sentinel.config.loader import load
    from sentinel.registry import ProviderRegistry

    try:
        cfg = load(config)
        registry = ProviderRegistry.from_config(cfg.__dict__)
    except Exception as e:
        click.echo(f"  ❌  {e}")
        raise SystemExit(1)

    table_name = f"{cfg.namespace}-sentinel-incidents-{cfg.cloud_provider.provider}"
    click.echo(f"\n  📊  Sentinel Status — namespace: {cfg.namespace}\n")

    try:
        incidents = registry.cloud.query_items(
            table=table_name,
            index="severity-created-index",
            key_condition={"status": "OPEN"},
        )
        incidents = sorted(incidents, key=lambda x: x.get("created_at", ""), reverse=True)[:last]
    except Exception:
        incidents = []

    if not incidents:
        click.echo("  No open incidents found.\n")
        return

    click.echo(f"  {'ID':<38} {'Severity':<8} {'Service':<20} {'Status'}")
    click.echo("  " + "-" * 76)
    for inc in incidents:
        click.echo(
            f"  {inc.get('alert_id', 'n/a'):<38} "
            f"{inc.get('severity', 'n/a'):<8} "
            f"{inc.get('service_name', 'n/a'):<20} "
            f"{inc.get('status', 'n/a')}"
        )
    click.echo()

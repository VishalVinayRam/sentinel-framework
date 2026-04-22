import click

from sentinel.cli.init import init_command
from sentinel.cli.validate import validate_command
from sentinel.cli.status import status_command
from sentinel.cli.grafana import grafana_group


@click.group()
@click.version_option(package_name="sentinel-framework")
def main():
    """Sentinel — AI-powered incident response framework."""


main.add_command(init_command, name="init")
main.add_command(validate_command, name="validate")
main.add_command(status_command, name="status")
main.add_command(grafana_group, name="grafana")

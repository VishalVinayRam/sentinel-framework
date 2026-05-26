"""
sentinel grafana — manage Sentinel dashboards in an external Grafana instance.

Commands:
  sentinel grafana push   — import/update the Sentinel dashboard
  sentinel grafana open   — print the dashboard URL
"""

import json
from pathlib import Path

import click
import requests


DASHBOARD_PATH = Path(__file__).parent.parent.parent / "observability" / "grafana" / "dashboards" / "sentinel-incidents.json"


@click.group("grafana")
def grafana_group():
    """Manage Sentinel dashboards in an external Grafana."""


@grafana_group.command("push")
@click.option("--url", envvar="GRAFANA_URL", required=True, help="Grafana base URL, e.g. https://grafana.company.com")
@click.option("--api-key", envvar="GRAFANA_API_KEY", required=True, help="Grafana service account token")
@click.option("--folder", default="Sentinel", show_default=True, help="Dashboard folder name")
@click.option("--dashboard", default=str(DASHBOARD_PATH), show_default=True, help="Path to dashboard JSON")
def push(url: str, api_key: str, folder: str, dashboard: str):
    """Push the Sentinel dashboard to an existing Grafana instance."""
    url = url.rstrip("/")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # 1. Resolve or create the target folder
    folder_id = _get_or_create_folder(url, headers, folder)
    click.echo(f"  folder '{folder}' → id {folder_id}")

    # 2. Load dashboard JSON
    dash_path = Path(dashboard)
    if not dash_path.exists():
        raise click.ClickException(f"Dashboard file not found: {dash_path}")
    with dash_path.open() as f:
        dashboard_json = json.load(f)

    # Strip the id so Grafana treats this as upsert-by-uid
    dashboard_json.pop("id", None)

    # 3. Push
    payload = {
        "dashboard": dashboard_json,
        "folderId": folder_id,
        "overwrite": True,
        "message": "pushed by sentinel cli",
    }
    resp = requests.post(f"{url}/api/dashboards/db", headers=headers, json=payload, timeout=15)

    if resp.status_code not in (200, 201):
        raise click.ClickException(
            f"Grafana returned {resp.status_code}: {resp.text[:300]}"
        )

    result = resp.json()
    dashboard_url = url + result.get("url", "")
    click.echo("  dashboard pushed successfully")
    click.echo(f"  {dashboard_url}")


@grafana_group.command("open")
@click.option("--url", envvar="GRAFANA_URL", required=True, help="Grafana base URL")
def open_cmd(url: str):
    """Print the URL of the Sentinel dashboard in Grafana."""
    click.echo(f"{url.rstrip('/')}/d/sentinel-incidents/sentinel-incident-overview")


# ─── helpers ─────────────────────────────────────────────────────────────────

def _get_or_create_folder(url: str, headers: dict, title: str) -> int:
    resp = requests.get(f"{url}/api/folders", headers=headers, timeout=10)
    resp.raise_for_status()
    for folder in resp.json():
        if folder.get("title") == title:
            return folder["id"]

    # create it
    resp = requests.post(
        f"{url}/api/folders",
        headers=headers,
        json={"title": title},
        timeout=10,
    )
    if resp.status_code not in (200, 201):
        raise click.ClickException(
            f"Could not create Grafana folder '{title}': {resp.status_code} {resp.text[:200]}"
        )
    return resp.json()["id"]

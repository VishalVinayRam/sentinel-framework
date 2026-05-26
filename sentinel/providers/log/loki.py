"""
Loki logs provider — queries Grafana Loki via the HTTP API.

Works with:
  - Self-hosted Loki (Grafana OSS)
  - Grafana Cloud (set tenant_id + auth)
  - Loki in Kubernetes (port-forward loki:3100)

Usage:
    provider = LokiLogsProvider(
        base_url="http://localhost:3100",
        tenant_id="",           # empty for single-tenant
        username="",            # Grafana Cloud basic auth
        password="",
    )
    logs = provider.fetch_errors("payments-service", minutes=30)
    text = provider.fetch_as_text("payments-service")
"""

from dataclasses import dataclass
from typing import Optional

import requests


@dataclass
class LokiLogLine:
    timestamp_ns: int
    timestamp_iso: str
    message: str
    stream_labels: dict
    level: str = ""


class LokiLogsProvider:
    """Fetches recent error logs from Grafana Loki using LogQL."""

    def __init__(
        self,
        base_url: str = "http://localhost:3100",
        tenant_id: str = "",
        username: str = "",
        password: str = "",
        timeout: int = 10,
        # Label selectors — adjust to match your Loki label scheme
        service_label: str = "service",     # e.g. {service="payments-service"}
        fallback_labels: tuple = ("job", "app", "container"),
    ):
        self._url     = base_url.rstrip("/")
        self._headers = {}
        self._auth: Optional[tuple] = None
        self._timeout = timeout
        self._service_label  = service_label
        self._fallback_labels = fallback_labels

        if tenant_id:
            self._headers["X-Scope-OrgID"] = tenant_id
        if username:
            self._auth = (username, password)

    # ── Public API ─────────────────────────────────────────────────────────

    def is_ready(self) -> bool:
        try:
            r = requests.get(f"{self._url}/ready", headers=self._headers, auth=self._auth, timeout=3)
            return r.status_code == 200
        except Exception:
            return False

    def fetch_errors(
        self,
        service: str,
        minutes: int = 30,
        limit: int = 100,
        levels: tuple = ("error", "critical", "fatal", "warn"),
    ) -> list[LokiLogLine]:
        """Return recent log lines matching error-level patterns."""
        query   = self._build_query(service, levels)
        end_ns  = _now_ns()
        start_ns = end_ns - minutes * 60 * int(1e9)

        try:
            resp = requests.get(
                f"{self._url}/loki/api/v1/query_range",
                params={
                    "query":     query,
                    "start":     start_ns,
                    "end":       end_ns,
                    "limit":     limit,
                    "direction": "backward",
                },
                headers=self._headers,
                auth=self._auth,
                timeout=self._timeout,
            )
            resp.raise_for_status()
        except Exception as e:
            print(f"[loki] query_range failed: {e}")
            return []

        return _parse_query_range(resp.json())

    def fetch_as_text(self, service: str, minutes: int = 30, limit: int = 100) -> str:
        lines = self.fetch_errors(service, minutes, limit)
        return "\n".join(
            f"{entry.timestamp_iso}  [{entry.level}]  {entry.message}" for entry in lines
        )

    def query_raw(self, logql: str, minutes: int = 30, limit: int = 200) -> list[LokiLogLine]:
        """Execute an arbitrary LogQL query."""
        end_ns   = _now_ns()
        start_ns = end_ns - minutes * 60 * int(1e9)
        try:
            resp = requests.get(
                f"{self._url}/loki/api/v1/query_range",
                params={"query": logql, "start": start_ns, "end": end_ns, "limit": limit, "direction": "backward"},
                headers=self._headers,
                auth=self._auth,
                timeout=self._timeout,
            )
            resp.raise_for_status()
            return _parse_query_range(resp.json())
        except Exception as e:
            print(f"[loki] query_raw failed: {e}")
            return []

    def list_labels(self) -> list[str]:
        """Return all label names known to this Loki instance."""
        try:
            resp = requests.get(
                f"{self._url}/loki/api/v1/labels",
                headers=self._headers, auth=self._auth, timeout=self._timeout,
            )
            return resp.json().get("data", [])
        except Exception:
            return []

    def list_label_values(self, label: str) -> list[str]:
        """Return all values for a given label (e.g. all 'service' names)."""
        try:
            resp = requests.get(
                f"{self._url}/loki/api/v1/label/{label}/values",
                headers=self._headers, auth=self._auth, timeout=self._timeout,
            )
            return resp.json().get("data", [])
        except Exception:
            return []

    # ── Helpers ────────────────────────────────────────────────────────────

    def _build_query(self, service: str, levels: tuple) -> str:
        selector = f'{{{self._service_label}="{service}"}}'
        level_filter = "|".join(levels)
        # Log pipeline: filter by level keyword, then parse JSON if structured
        return (
            f'{selector} '
            f'|= ~"(?i)({level_filter})"'
        )


# ── Module-level helpers ───────────────────────────────────────────────────

def _now_ns() -> int:
    from datetime import datetime, timezone
    return int(datetime.now(timezone.utc).timestamp() * 1e9)


def _parse_query_range(body: dict) -> list[LokiLogLine]:
    lines = []
    results = body.get("data", {}).get("result", [])
    for stream in results:
        labels = stream.get("stream", {})
        for ts_ns_str, message in stream.get("values", []):
            ts_ns = int(ts_ns_str)
            ts_iso = _ns_to_iso(ts_ns)
            lines.append(LokiLogLine(
                timestamp_ns=ts_ns,
                timestamp_iso=ts_iso,
                message=message.strip(),
                stream_labels=labels,
                level=_detect_level_from_labels_or_message(labels, message),
            ))
    # Sort newest-first
    lines.sort(key=lambda entry: entry.timestamp_ns, reverse=True)
    return lines


def _ns_to_iso(ns: int) -> str:
    from datetime import datetime, timezone
    secs = ns / 1e9
    return datetime.fromtimestamp(secs, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _detect_level_from_labels_or_message(labels: dict, message: str) -> str:
    level = labels.get("level") or labels.get("severity") or labels.get("log_level") or ""
    if level:
        return level.upper()
    msg = message.upper()
    for kw in ("FATAL", "CRITICAL", "ERROR", "WARN", "WARNING", "INFO", "DEBUG"):
        if kw in msg:
            return kw
    return "UNKNOWN"
